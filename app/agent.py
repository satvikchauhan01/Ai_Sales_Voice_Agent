"""The sales agent: call sessions, system prompt (with lead memory), and one conversation turn.

Turn pipeline:  caller audio -> STT -> language choice -> LLM (+ tools) -> TTS -> audio back
"""
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

from app import analysis, crm, db, llm, speech
from app.config import BUSINESS, DEFAULT_LANGUAGE, LLM_FALLBACK_MODEL, LLM_MODEL, TIMEZONE, TTS_LANGUAGES
from app.qualification import CRITERIA
from app.tools import build_tools

log = logging.getLogger("agent")
IST = ZoneInfo(TIMEZONE)
MIN_WORDS_TO_SWITCH_LANGUAGE = 3

# Spoken when Gemini fails even after retries and the fallback model, so the call goes on
# (the caller just repeats) instead of the browser showing a 502 error.
SORRY = {
    "en-IN": "Sorry, I had a small network problem. Could you please say that again?",
    "hi-IN": "माफ़ कीजिए, नेटवर्क में थोड़ी दिक्कत हुई। क्या आप दोबारा बता सकते हैं?",
}


@dataclass
class CallSession:
    call_id: int
    lead_id: int
    language: str               # language the agent currently speaks
    language_locked: bool       # True when the caller picked a language in the form
    chat: object                # Gemini chat (keeps short-term conversation memory)
    system_prompt: str          # kept so the chat can be rebuilt on the fallback model
    tools: list
    model: str = LLM_MODEL      # model this call currently uses
    user_turns: int = 0
    started: float = field(default_factory=time.time)
    lock: threading.Lock = field(default_factory=threading.Lock)


# In-memory session store. Fine for one server; at scale this state moves to Redis
# (or a LiveKit worker per call) - see docs/SCALING.md.
SESSIONS: dict[int, CallSession] = {}


# ---------------- Prompt ----------------
def build_memory(lead: dict) -> str:
    """Long-term memory: what we already know about this lead from earlier calls."""
    lines = []
    bant = json.loads(lead["bant_json"] or "{}")
    if bant:
        known = [f"{k}: {'yes' if v['qualified'] else 'no'} ({v['note']})" for k, v in bant.items()]
        lines.append("Qualification so far: " + "; ".join(known))
    missing = [c for c in CRITERIA if c not in bant]
    if missing:
        lines.append("Still unknown: " + ", ".join(missing))

    previous_calls = [c for c in db.list_calls(lead_id=lead["id"], limit=4) if c["summary"]]
    if not previous_calls:
        # Say it explicitly, otherwise the model may invent an earlier conversation.
        lines.insert(0, "This is the FIRST call with this lead. There were no earlier calls.")
    for call in previous_calls[:3]:
        lines.append(f"Earlier call ({call['started_at'][:10]}): {call['summary']}")

    for appt in db.list_appointments(lead_id=lead["id"]):
        start = datetime.fromisoformat(appt["start_time"]).strftime("%a %d %b %I:%M %p")
        lines.append(f"Existing booked site survey: {start}")

    if not lines:
        return "This is a new lead. No previous conversations."
    return "\n".join(lines)


def build_system_prompt(lead: dict) -> str:
    now = datetime.now(IST)
    b = BUSINESS
    return f"""You are {b['agent_name']}, a friendly voice sales assistant for {b['company']}, which sells {b['product']} in {b['region']}.
You are an AI assistant and you say so when you introduce yourself.

TODAY: {now.strftime('%A %d %B %Y, %I:%M %p')} (India time). Use this to resolve words like "tomorrow".

CALLER: {lead['name']} | phone {lead['phone']} | email {lead['email'] or 'not given'}
WHAT WE KNOW (memory from earlier calls):
{build_memory(lead)}

GOAL: understand the caller's needs, answer questions, qualify the lead, and book a {b['appointment']}.

HOW TO QUALIFY (BANT) - ask naturally, one question at a time, never as a checklist:
- need: why they want solar (high electricity bill, power cuts, savings)? roof available?
- budget: monthly electricity bill and comfortable budget or interest in EMI
- authority: are they the owner / decision maker?
- timeline: when do they want installation?
Call update_qualification every time the caller reveals one of these.

RULES:
1. This is a phone-style VOICE call. Reply in 1-2 short sentences (max ~35 words). No lists, no markdown, no emojis.
2. For ANY fact about products, prices, subsidy, warranty or financing, call search_knowledge_base first. Never invent numbers. Search ONCE per caller question and answer from those results; do not search again with reworded queries. If the results have no answer, say the engineer will confirm during the survey.
3. Reply in the language the caller uses (each message is tagged with it), written in that language's native script (Hindi in Devanagari, Tamil in Tamil script, etc.) so the voice sounds natural. Keep brand names and numbers simple.
4. When the caller is interested, offer the free site survey: call get_available_slots, offer at most two options, and call book_appointment only after the caller confirms one slot.
5. If the caller is not interested, be polite, don't push, and close the call warmly.
6. Speak prices like "two lakh ten thousand rupees" rather than symbols.
"""


# ---------------- Language ----------------
def choose_language(current: str, locked: bool, detected: str | None, text: str, user_turns: int) -> str:
    """Decide which language the agent should speak next.

    - Locked (caller chose in the form): never switch.
    - First caller message: follow what STT detected.
    - Later: switch only for messages with >= 3 words, because very short words
      ("ok", "haan") are often mis-detected.
    """
    if locked:
        return current
    if detected is None or detected not in TTS_LANGUAGES:
        return current
    if detected == current:
        return current
    if user_turns <= 1:
        return detected
    if len(text.split()) >= MIN_WORDS_TO_SWITCH_LANGUAGE:
        return detected
    return current


# ---------------- Call lifecycle ----------------
def _lead_snapshot(lead_id: int) -> dict:
    lead = db.get_lead(lead_id)
    return {"score": lead["score"], "status": lead["status"], "bant": json.loads(lead["bant_json"] or "{}")}


def _speak(text: str, language: str) -> str:
    return speech.synthesize(text, language)


def _send(session: CallSession, message: str):
    """Send one message to Gemini. If the main model is overloaded (after its retries),
    move this call to LLM_FALLBACK_MODEL, keeping the whole conversation history."""
    try:
        return session.chat.send_message(message)
    except Exception as exc:
        if not llm.is_temporary_error(exc) or not LLM_FALLBACK_MODEL or session.model == LLM_FALLBACK_MODEL:
            raise
        log.warning("call %s: %s failed (%s) - switching to %s",
                    session.call_id, session.model, str(exc)[:80], LLM_FALLBACK_MODEL)
        session.chat = llm.create_chat(session.system_prompt, session.tools,
                                       model=LLM_FALLBACK_MODEL, history=session.chat.get_history())
        session.model = LLM_FALLBACK_MODEL
        return session.chat.send_message(message)


def start_call(name: str, phone: str, email: str, language: str) -> dict:
    lead = db.upsert_lead(name.strip(), phone.strip(), email.strip(), language)

    if language in TTS_LANGUAGES:
        current_language = language
        locked = True
    else:
        current_language = DEFAULT_LANGUAGE
        locked = False

    returning_lead = len(db.list_calls(lead_id=lead["id"], limit=1)) > 0
    call_id = db.create_call(lead["id"], current_language)
    system_prompt = build_system_prompt(lead)
    tools = build_tools(lead["id"], call_id)
    session = CallSession(call_id=call_id, lead_id=lead["id"], language=current_language,
                          language_locked=locked, chat=llm.create_chat(system_prompt, tools),
                          system_prompt=system_prompt, tools=tools)
    SESSIONS[call_id] = session

    language_name = TTS_LANGUAGES[current_language]
    greeting = ""
    try:
        response = _send(
            session,
            f"[CALL CONNECTED. Speak {language_name}.] Greet {lead['name']} by name, introduce yourself "
            f"and {BUSINESS['company']}, mention you are an AI assistant, and ask how you can help. "
            f"Only if memory lists an 'Earlier call', refer to it briefly; otherwise do not mention any earlier conversation.",
        )
        greeting = (response.text or "").strip()
    except Exception as exc:
        if not llm.is_temporary_error(exc):
            raise
        log.error("call %s: greeting failed on all models: %s", call_id, exc)
    if not greeting:  # model down or empty reply -> simple fixed greeting, the call still starts
        greeting = (f"Hello {lead['name']}, this is {BUSINESS['agent_name']}, an AI assistant from "
                    f"{BUSINESS['company']}. How can I help you today?")
    db.add_turn(call_id, "agent", greeting, current_language)

    return {
        "call_id": call_id,
        "lead_id": lead["id"],
        "returning_lead": returning_lead,
        "agent_text": greeting,
        "audio_b64": _speak(greeting, current_language),
        "language": current_language,
        "lead": _lead_snapshot(lead["id"]),
    }


def _respond(session: CallSession, user_text: str, detected_language: str | None) -> dict:
    """Shared by voice turns and text turns: language choice -> LLM -> TTS."""
    session.user_turns += 1
    session.language = choose_language(
        session.language, session.language_locked, detected_language, user_text, session.user_turns
    )
    db.add_turn(session.call_id, "user", user_text, detected_language or session.language)

    language_name = TTS_LANGUAGES[session.language]
    t0 = time.time()
    try:
        response = _send(session, f"[Caller speaks {language_name}. Reply in {language_name}.] {user_text}")
        reply = (response.text or "").strip()
    except Exception as exc:
        if not llm.is_temporary_error(exc):
            raise  # a real bug: let it show up as an error
        log.error("call %s: LLM failed on all models: %s", session.call_id, exc)
        reply = ""
    if not reply:
        reply = SORRY.get(session.language, SORRY["en-IN"])
    llm_ms = int((time.time() - t0) * 1000)

    db.add_turn(session.call_id, "agent", reply, session.language)

    t1 = time.time()
    audio = _speak(reply, session.language)
    tts_ms = int((time.time() - t1) * 1000)

    return {
        "user_text": user_text,
        "agent_text": reply,
        "audio_b64": audio,
        "language": session.language,
        "lead": _lead_snapshot(session.lead_id),
        "latency_ms": {"llm": llm_ms, "tts": tts_ms},
    }


def get_session(call_id: int) -> CallSession:
    session = SESSIONS.get(call_id)
    if session is None:
        raise KeyError(f"No active call {call_id}")
    return session


def handle_audio_turn(call_id: int, wav_bytes: bytes) -> dict:
    session = get_session(call_id)
    if not session.lock.acquire(blocking=False):
        return {"busy": True}
    try:
        t0 = time.time()
        if session.language_locked:
            stt_language = session.language
        else:
            stt_language = "unknown"
        stt = speech.transcribe(wav_bytes, stt_language)
        stt_ms = int((time.time() - t0) * 1000)
        if not stt["text"]:
            return {"empty": True}
        result = _respond(session, stt["text"], stt["language"])
        result["latency_ms"]["stt"] = stt_ms
        log.info("call %s latency %s", call_id, result["latency_ms"])
        return result
    finally:
        session.lock.release()


def handle_text_turn(call_id: int, text: str) -> dict:
    """Same as a voice turn but skips STT. Useful for debugging and as a mic fallback."""
    session = get_session(call_id)
    with session.lock:
        return _respond(session, text.strip(), None)


def end_call(call_id: int) -> dict:
    session = SESSIONS.pop(call_id, None)
    call = db.get_call(call_id)
    if call is None:
        raise KeyError(f"Unknown call {call_id}")

    duration = 0
    if session is not None:
        duration = int(time.time() - session.started)
    db.update_call(call_id, ended_at=db.now_iso(), duration_sec=duration)

    result = analysis.analyze_call(call_id)
    crm_result = crm.sync_call(call["lead_id"], call_id, result.get("summary", ""))
    db.update_call(call_id, crm_sync_json=json.dumps(crm_result))
    result["crm"] = crm_result
    result["lead"] = _lead_snapshot(call["lead_id"])
    return result
