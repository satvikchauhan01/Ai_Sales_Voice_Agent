"""Speech layer: Sarvam AI speech-to-text (Saaras) and text-to-speech (Bulbul) over REST.

Both are plain HTTP calls, so they are easy to debug: print the status code and body.
"""
import logging
import re

import httpx

from app.config import (
    DEFAULT_LANGUAGE,
    SARVAM_API_KEY,
    STT_MODEL,
    TTS_LANGUAGES,
    TTS_MODEL,
    TTS_SPEAKER,
)

log = logging.getLogger("speech")
SARVAM_BASE_URL = "https://api.sarvam.ai"
TTS_MAX_CHARS = 2500  # API limit per request


class SpeechError(Exception):
    pass


def _headers() -> dict:
    if not SARVAM_API_KEY:
        raise SpeechError("SARVAM_API_KEY is missing in .env")
    return {"api-subscription-key": SARVAM_API_KEY}


def transcribe(wav_bytes: bytes, language_code: str = "unknown") -> dict:
    """Audio (WAV, 16 kHz from the browser VAD) -> {"text": ..., "language": ...}.

    language_code="unknown" asks Sarvam to auto-detect the language.
    """
    files = {"file": ("speech.wav", wav_bytes, "audio/wav")}
    data = {"model": STT_MODEL, "language_code": language_code}
    if STT_MODEL.startswith("saaras"):
        data["mode"] = "transcribe"  # mode only exists for saaras models

    response = httpx.post(
        f"{SARVAM_BASE_URL}/speech-to-text",
        headers=_headers(),
        files=files,
        data=data,
        timeout=30,
    )
    if response.status_code != 200:
        raise SpeechError(f"STT failed ({response.status_code}): {response.text[:300]}")

    body = response.json()
    text = (body.get("transcript") or "").strip()
    language = body.get("language_code")
    return {"text": text, "language": language}


def clean_for_speech(text: str) -> str:
    """Remove markdown symbols the LLM might add, so TTS does not read them out."""
    text = re.sub(r"[*_#`>|]", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def synthesize(text: str, language_code: str) -> str:
    """Text -> base64-encoded WAV string (ready for the browser to play)."""
    if language_code not in TTS_LANGUAGES:
        language_code = DEFAULT_LANGUAGE

    payload = {
        "text": clean_for_speech(text)[:TTS_MAX_CHARS],
        "language_code": language_code,
        "speaker": TTS_SPEAKER,
        "model": TTS_MODEL,
    }
    response = httpx.post(
        f"{SARVAM_BASE_URL}/text-to-speech", headers=_headers(), json=payload, timeout=30
    )

    # Older API versions named the field target_language_code. Retry once if needed.
    if response.status_code in (400, 422) and "language_code" in response.text:
        log.warning("TTS rejected language_code, retrying with target_language_code")
        payload["target_language_code"] = payload.pop("language_code")
        response = httpx.post(
            f"{SARVAM_BASE_URL}/text-to-speech", headers=_headers(), json=payload, timeout=30
        )

    if response.status_code != 200:
        raise SpeechError(f"TTS failed ({response.status_code}): {response.text[:300]}")

    audios = response.json().get("audios") or []
    if not audios:
        raise SpeechError("TTS returned no audio")
    return audios[0]
