"""Fakes for Sarvam + Gemini so the whole pipeline can be tested offline.

Gemini is faked at the lowest level (the raw HTTP call inside google-genai), so the REAL
automatic-function-calling loop and our REAL tool functions still run.
"""
import base64
import hashlib
import io
import json
import re
import wave
from datetime import datetime, timedelta

from google.genai import models, types

from app import booking, llm, speech


def _silent_wav_b64(seconds: float = 0.3) -> str:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * int(16000 * seconds))
    return base64.b64encode(buffer.getvalue()).decode()


def _text(text: str) -> types.GenerateContentResponse:
    return types.GenerateContentResponse(
        candidates=[types.Candidate(content=types.Content(role="model", parts=[types.Part(text=text)]))]
    )


def _call(name: str, args: dict) -> types.GenerateContentResponse:
    part = types.Part(function_call=types.FunctionCall(name=name, args=args))
    return types.GenerateContentResponse(candidates=[types.Candidate(content=types.Content(role="model", parts=[part]))])


def next_working_day() -> str:
    day = datetime.now(booking.IST).date() + timedelta(days=1)
    while day.weekday() == 6:
        day += timedelta(days=1)
    return day.isoformat()


ANALYSIS_JSON = {
    "summary": "Caller with a 4,000 rupee monthly bill asked about the 5 kW system price and booked a free site survey.",
    "sentiment": "positive",
    "sentiment_score": 0.7,
    "sentiment_trend": "curious at start, positive at the end",
    "objections": ["price too high"],
    "buying_signals": ["asked about EMI", "booked survey"],
    "interested_in": "5 kW on-grid",
    "next_step": "Engineer visit; prepare EMI options",
    "outcome": "follow_up",
}


def fake_generate(self, *, model, contents, config=None):
    """Scripted 'LLM' that decides what to do from the last message."""
    if config is not None and getattr(config, "response_schema", None) is not None:
        return _text(json.dumps(ANALYSIS_JSON))

    last = contents[-1] if isinstance(contents, list) else contents
    if isinstance(last, str):  # plain-text prompt = business insights request
        return _text("1. Most callers ask about price first: mention EMI early.\n"
                     "2. Price is the top objection: lead with subsidy and payback.\n"
                     "3. Callers with bills above 4,000 rupees book surveys most often.\n"
                     "4. Offer two survey slots, not one, to raise the booking rate.")
    parts = last.parts or []
    function_responses = [p.function_response for p in parts if p.function_response]

    if function_responses:
        name = function_responses[-1].name
        result = str((function_responses[-1].response or {}).get("result", ""))
        if name == "update_qualification":
            return _text("Thanks! A 5 kW system suits that bill. Would you like a free site survey?")
        if name == "search_knowledge_base":
            return _text("The 5 kW system is about two lakh ninety thousand rupees before subsidy.")
        if name == "get_available_slots":
            match = re.search(r"slot_start=(\S+)", result)
            if match:
                return _call("book_appointment", {"slot_start": match.group(1), "email": "rahul@example.com"})
            return _text("Sorry, no slots on that day.")
        if name == "book_appointment":
            return _text("Done, your free site survey is booked. " + result[:60])

    text = " ".join(p.text or "" for p in parts)
    if "CALL CONNECTED" in text:
        return _text("Hello! I am Priya, an AI assistant from SunRise Solar. How can I help you today?")
    if "bill" in text.lower():
        return _call("update_qualification", {"criterion": "budget", "qualified": True, "note": "bill 4000"})
    if "owner" in text.lower():
        return _call("update_qualification", {"criterion": "authority", "qualified": True, "note": "owns house"})
    if "price" in text.lower() or "कीमत" in text:
        return _call("search_knowledge_base", {"question": "5 kW system price"})
    if "book" in text.lower():
        return _call("get_available_slots", {"date": next_working_day()})
    return _text("Okay, tell me more.")


def fake_embed(texts):
    vectors = []
    for text in texts:
        digest = hashlib.sha256(text.encode()).digest()
        vectors.append([b / 255 for b in digest[:16]])
    return vectors


SCRIPTED_TRANSCRIPTS = [
    {"text": "My electricity bill is about 4000 rupees", "language": "en-IN"},
    {"text": "What is the price of the 5 kW system?", "language": "en-IN"},
    {"text": "Yes please book a survey tomorrow", "language": "en-IN"},
]


def install(monkeypatch=None):
    """Patch external services. Works with pytest's monkeypatch or standalone."""
    counter = {"i": 0}

    def fake_transcribe(wav_bytes, language_code="unknown"):
        item = SCRIPTED_TRANSCRIPTS[counter["i"] % len(SCRIPTED_TRANSCRIPTS)]
        counter["i"] += 1
        return dict(item)

    def fake_synthesize(text, language_code):
        return _silent_wav_b64()

    patches = [
        (models.Models, "_generate_content_with_continuation", fake_generate),
        (speech, "transcribe", fake_transcribe),
        (speech, "synthesize", fake_synthesize),
        (llm, "embed", fake_embed),
    ]
    for target, name, value in patches:
        if monkeypatch is not None:
            monkeypatch.setattr(target, name, value)
        else:
            setattr(target, name, value)
