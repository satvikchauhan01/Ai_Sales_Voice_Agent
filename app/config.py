"""All settings in one place. Values come from .env so nothing secret lives in code."""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

# ---------- Required API keys (both have free tiers) ----------
SARVAM_API_KEY = os.getenv("SARVAM_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# ---------- Models (change in .env without touching code) ----------
LLM_MODEL = os.getenv("LLM_MODEL", "gemini-3.5-flash-lite")
# Used for the rest of a call when LLM_MODEL is overloaded (503/504/429). Empty = no fallback.
LLM_FALLBACK_MODEL = os.getenv("LLM_FALLBACK_MODEL", "gemini-3.1-flash-lite")
ANALYSIS_MODEL = os.getenv("ANALYSIS_MODEL", "gemini-3.5-flash-lite")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "gemini-embedding-001")
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "768"))
# minimal | low | medium | high. Empty string = model default.
# "low" is safer than "minimal" for reliable tool calling.
LLM_THINKING_LEVEL = os.getenv("LLM_THINKING_LEVEL", "low")
# Per-request limit for live-call LLM requests; a slow request is cancelled and retried (max 3 tries).
# Gemini rejects deadlines below 10 s (400 INVALID_ARGUMENT), so never go lower than 10.
LLM_TIMEOUT_SECONDS = max(10, int(os.getenv("LLM_TIMEOUT_SECONDS", "10")))

STT_MODEL = os.getenv("STT_MODEL", "saaras:v4")
TTS_MODEL = os.getenv("TTS_MODEL", "bulbul:v3")
TTS_SPEAKER = os.getenv("TTS_SPEAKER", "priya")

# ---------- Optional integrations (local fallback when empty) ----------
HUBSPOT_ACCESS_TOKEN = os.getenv("HUBSPOT_ACCESS_TOKEN", "")
CAL_API_KEY = os.getenv("CAL_API_KEY", "")
CAL_EVENT_TYPE_ID = os.getenv("CAL_EVENT_TYPE_ID", "")

# ---------- App ----------
TIMEZONE = "Asia/Kolkata"
DB_PATH = Path(os.getenv("DB_PATH", str(BASE_DIR / "data" / "app.db")))
KB_DIR = BASE_DIR / "knowledge_base"
STATIC_DIR = BASE_DIR / "static"

# ---------- The (fictional) business the agent sells for ----------
BUSINESS = {
    "company": "SunRise Solar",
    "agent_name": "Priya",
    "product": "rooftop solar systems for homes and small businesses",
    "region": "Delhi NCR and Uttar Pradesh",
    "appointment": "free home site survey (about 45 minutes, done by an engineer)",
}

# ---------- Lead qualification (BANT) ----------
# Change weights/thresholds here; scoring code reads them.
BANT_WEIGHTS = {"budget": 30, "authority": 20, "need": 30, "timeline": 20}
HOT_THRESHOLD = 70
WARM_THRESHOLD = 40

# ---------- Languages ----------
# Bulbul TTS supports these 11 languages; STT understands more,
# but the agent can only *speak* these.
TTS_LANGUAGES = {
    "en-IN": "English",
    "hi-IN": "Hindi",
    "bn-IN": "Bengali",
    "gu-IN": "Gujarati",
    "kn-IN": "Kannada",
    "ml-IN": "Malayalam",
    "mr-IN": "Marathi",
    "od-IN": "Odia",
    "pa-IN": "Punjabi",
    "ta-IN": "Tamil",
    "te-IN": "Telugu",
}
DEFAULT_LANGUAGE = "en-IN"


def hubspot_enabled() -> bool:
    return bool(HUBSPOT_ACCESS_TOKEN)


def calcom_enabled() -> bool:
    return bool(CAL_API_KEY and CAL_EVENT_TYPE_ID)
