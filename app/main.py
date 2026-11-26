"""FastAPI app: REST API + serves the two web pages (call page and dashboard).

Run:  uvicorn app.main:app --reload   then open http://localhost:8000
"""
import json
import logging
import mimetypes
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import agent, analysis, db, rag
from app.config import (
    BUSINESS,
    GEMINI_API_KEY,
    LLM_MODEL,
    SARVAM_API_KEY,
    STATIC_DIR,
    TTS_LANGUAGES,
    calcom_enabled,
    hubspot_enabled,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
log = logging.getLogger("main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    try:
        rag.ensure_ingested()
    except Exception as exc:
        log.error("Knowledge base ingest failed: %s", exc)
    yield


# Windows sometimes maps .js/.mjs to text/plain in the registry; browsers then refuse to
# load the voice-activity-detection worker. Force correct types.
mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("application/javascript", ".mjs")
mimetypes.add_type("application/wasm", ".wasm")

app = FastAPI(title="AI Sales Voice Agent", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class StartCallRequest(BaseModel):
    name: str
    phone: str
    email: str = ""
    language: str = "auto"


class TextTurnRequest(BaseModel):
    text: str


def _error(exc: Exception) -> HTTPException:
    log.exception("Request failed")
    return HTTPException(status_code=502, detail=str(exc))


# ---------------- Pages ----------------
@app.get("/")
def call_page():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/dashboard")
def dashboard_page():
    return FileResponse(STATIC_DIR / "dashboard.html")


@app.get("/favicon.ico")
def favicon():
    return Response(status_code=204)


# ---------------- API ----------------
@app.get("/api/health")
def health():
    return {
        "company": BUSINESS["company"],
        "llm_model": LLM_MODEL,
        "gemini_key": bool(GEMINI_API_KEY),
        "sarvam_key": bool(SARVAM_API_KEY),
        "crm": "hubspot + local" if hubspot_enabled() else "local",
        "calendar": "cal.com" if calcom_enabled() else "local",
        "languages": TTS_LANGUAGES,
    }


@app.post("/api/calls/start")
def start_call(req: StartCallRequest):
    if not req.name.strip() or not req.phone.strip():
        raise HTTPException(status_code=400, detail="Name and phone are required")
    try:
        return agent.start_call(req.name, req.phone, req.email, req.language)
    except Exception as exc:
        raise _error(exc)


@app.post("/api/calls/{call_id}/turn")
async def audio_turn(call_id: int, audio: UploadFile = File(...)):
    wav_bytes = await audio.read()
    try:
        return agent.handle_audio_turn(call_id, wav_bytes)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise _error(exc)


@app.post("/api/calls/{call_id}/text")
def text_turn(call_id: int, req: TextTurnRequest):
    if not req.text.strip():
        raise HTTPException(status_code=400, detail="Text is empty")
    try:
        return agent.handle_text_turn(call_id, req.text)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise _error(exc)


@app.post("/api/calls/{call_id}/end")
def end_call(call_id: int):
    try:
        return agent.end_call(call_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise _error(exc)


@app.get("/api/calls/{call_id}")
def call_detail(call_id: int):
    call = db.get_call(call_id)
    if call is None:
        raise HTTPException(status_code=404, detail="Call not found")
    call["analysis"] = json.loads(call["analysis_json"] or "{}")
    call["crm_sync"] = json.loads(call["crm_sync_json"] or "{}")
    call["turns"] = db.get_turns(call_id)
    return call


@app.get("/api/dashboard")
def dashboard_data():
    return analysis.dashboard_metrics()


@app.post("/api/insights")
def insights():
    try:
        return {"insights": analysis.business_insights()}
    except Exception as exc:
        raise _error(exc)


@app.post("/api/kb/reingest")
def reingest():
    try:
        return rag.ingest()
    except Exception as exc:
        raise _error(exc)


@app.get("/api/kb/search")
def kb_search(q: str):
    return rag.search(q)
