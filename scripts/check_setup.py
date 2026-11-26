"""Check every external service with one tiny real request. Run this FIRST after adding keys.

    python -m scripts.check_setup

Prints OK / FAIL for: Gemini chat, Gemini tool-calling, Gemini embeddings,
Sarvam TTS, Sarvam STT (round trip of the TTS audio), and optional HubSpot / Cal.com.
"""
import base64
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import booking, config, db, llm, speech  # noqa: E402


def check(name, fn):
    try:
        result = fn()
        print(f"[ OK ] {name}: {result}")
        return True
    except Exception as exc:
        print(f"[FAIL] {name}: {exc}")
        return False


def gemini_chat():
    chat = llm.create_chat("You are a test assistant. Reply in at most 5 words.", tools=[])
    return chat.send_message("Say hello").text


def gemini_tools():
    calls = []

    def get_weather(city: str) -> str:
        """Get the weather for a city.

        Args:
            city: City name.
        """
        calls.append(city)
        return "Sunny, 25 C"

    chat = llm.create_chat("Use tools when helpful. Reply in one short sentence.", tools=[get_weather])
    text = chat.send_message("What is the weather in Noida?").text
    if not calls:
        raise RuntimeError(f"model did not call the tool (reply: {text}). Try LLM_THINKING_LEVEL=medium")
    return f"tool called with {calls[0]!r} -> {text}"


def gemini_embed():
    vector = llm.embed(["rooftop solar price"])[0]
    return f"{len(vector)} dimensions"


audio_holder = {}


def sarvam_tts():
    audio_b64 = speech.synthesize("Hello, this is a test of the sales agent.", "en-IN")
    audio_holder["wav"] = base64.b64decode(audio_b64)
    return f"{len(audio_holder['wav'])} bytes of audio"


def sarvam_stt():
    if "wav" not in audio_holder:
        raise RuntimeError("skipped because TTS failed")
    result = speech.transcribe(audio_holder["wav"], "unknown")
    return f"{result['text']!r} (detected {result['language']})"


def calendar():
    from datetime import datetime, timedelta

    db.init_db()  # the server creates tables on startup; this script runs without the server
    day = (datetime.now(booking.IST) + timedelta(days=1)).date().isoformat()
    slots = booking.get_slots(day)
    source = "cal.com" if config.calcom_enabled() else "local calendar"
    return f"{len(slots)} slots tomorrow via {source}"


def hubspot():
    if not config.hubspot_enabled():
        return "not configured (optional) - local CRM will be used"
    import httpx

    r = httpx.get(
        "https://api.hubapi.com/crm/v3/objects/contacts?limit=1",
        headers={"Authorization": f"Bearer {config.HUBSPOT_ACCESS_TOKEN}"},
        timeout=15,
    )
    if r.status_code != 200:
        raise RuntimeError(f"{r.status_code} {r.text[:200]}")
    return "token works"


if __name__ == "__main__":
    print(f"LLM={config.LLM_MODEL}  STT={config.STT_MODEL}  TTS={config.TTS_MODEL}/{config.TTS_SPEAKER}\n")
    results = [
        check("Gemini chat", gemini_chat),
        check("Gemini tool calling", gemini_tools),
        check("Gemini embeddings", gemini_embed),
        check("Sarvam text-to-speech", sarvam_tts),
        check("Sarvam speech-to-text", sarvam_stt),
        check("Calendar", calendar),
        check("HubSpot", hubspot),
    ]
    print("\nAll good!" if all(results) else "\nFix the FAIL lines above (see README > Troubleshooting).")
