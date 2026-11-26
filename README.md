# Autonomous AI Sales Voice Agent (MVP)

A voice sales agent for a (fictional) rooftop-solar company, **SunRise Solar**. It talks to a lead in the
browser, answers product questions from a knowledge base (RAG), qualifies the lead (BANT), books a site
survey, syncs the CRM, and produces a call summary, sentiment and business insights.

Everything runs on **free tiers**: Gemini API (free tier), Sarvam AI (free starting credits), SQLite,
optional free HubSpot and Cal.com accounts.

| Requirement | Where it lives |
|---|---|
| Real-time voice (STT + TTS) | Browser VAD (`static/call.js`) -> Sarvam Saaras STT -> Gemini -> Sarvam Bulbul TTS (`app/speech.py`, `app/agent.py`) |
| Contextual memory | Short-term: Gemini chat history. Long-term: lead's BANT + past call summaries + bookings injected into the prompt (`build_memory` in `app/agent.py`) |
| Multilingual | Auto language detection by STT; agent replies + speaks in the caller's language (11 Indian languages incl. Hindi/English) (`choose_language`) |
| Lead qualification | LLM calls `update_qualification` tool; deterministic BANT score in `app/qualification.py` |
| Sentiment analysis | Post-call structured JSON: sentiment, score, trend, objections, buying signals (`app/analysis.py`) |
| RAG | `knowledge_base/*.md` -> chunks -> Gemini embeddings -> cosine search (`app/rag.py`), tool `search_knowledge_base` |
| Appointment booking | Tools `get_available_slots` + `book_appointment`; Cal.com if configured, else local calendar (`app/booking.py`) |
| CRM integration | Local CRM (leads table + pipeline status) always; HubSpot contact/deal/note sync if token set (`app/crm.py`) |
| Call summary + business insights | Summary per call + dashboard KPIs, top objections, AI insights (`/dashboard`) |

Docs: [Architecture](docs/ARCHITECTURE.md) | [Scaling to 1 lakh users, security, costs](docs/SCALING.md) | [Demo script + live-coding prep](docs/DEMO_SCRIPT.md)

---

## 1. Get the free API keys (10 minutes)

1. **Gemini**: open https://aistudio.google.com/apikey -> *Create API key* (no card needed).
2. **Sarvam AI**: sign up at https://dashboard.sarvam.ai -> copy the API key (comes with free credits).
3. *(Optional)* HubSpot / Cal.com - see section 5. Not needed for the demo.

## 2. Install and run (Windows)

Needs **Python 3.11+** (check with `py --version`). Commands for PowerShell (VS Code terminal):

```powershell
cd ai-sales-voice-agent
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
notepad .env
```

Paste `GEMINI_API_KEY` and `SARVAM_API_KEY` into `.env` and save. Then:

```powershell
python -m scripts.check_setup
uvicorn app.main:app --reload
```

`check_setup` sends one tiny real request to every API and prints OK/FAIL - run it before the demo.
If `Activate.ps1` is blocked, run `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` first
(in Command Prompt use `.venv\Scripts\activate.bat` instead).

Open **http://localhost:8000** in Chrome -> fill name + phone -> **Start call** -> allow the microphone -> talk.
Dashboard: **http://localhost:8000/dashboard**.

(macOS/Linux: `python3 -m venv .venv`, `source .venv/bin/activate`, `cp .env.example .env`.)

**Use headphones** for the demo so the agent's voice is not picked up by your mic.

## 3. Try this conversation (the end-to-end workflow)

1. "Hi, I want to know about solar for my house."
2. "My electricity bill is around 4000 rupees a month." -> budget/need get scored
3. "How much does a 5 kW system cost?" -> RAG answer from the knowledge base
4. "Haan, main hi ghar ka owner hoon. Mujhe agle mahine tak lagwana hai." -> Hindi; authority + timeline
5. "Can you book a survey tomorrow?" -> agent lists slots -> "The 11 AM one" -> booked
6. Click **End call** -> summary, sentiment, objections, CRM sync -> open the dashboard
7. Start a new call with the **same phone number** -> the agent remembers the previous call

## 4. Tests (no API keys needed)

```powershell
python -m pytest -q
python -m tests.run_with_fakes
```

`run_with_fakes` starts the UI with a scripted fake AI (no keys, no credits) - handy for UI work.

`tests/test_api_flow.py` runs the whole sales workflow through the real API and the real Gemini
function-calling loop, with only the network calls to Gemini/Sarvam faked.

## 5. Optional integrations

**HubSpot (free CRM)**: create a free account -> Settings -> *Development* -> *Legacy apps* ->
*Create legacy app* -> *Private* -> scopes: contacts read/write, deals read/write -> copy the access
token to `HUBSPOT_ACCESS_TOKEN`. After each call: contact upsert, deal in stage
*Appointment scheduled* / *Qualified to buy*, and a note with the AI summary.

**Cal.com (free calendar)**: create a free account and an event type (e.g. "Site survey", 45 min) ->
Settings -> Developer -> API keys -> set `CAL_API_KEY` and `CAL_EVENT_TYPE_ID`. The agent then reads
real availability and creates real bookings.

## 6. Useful endpoints (for debugging / live coding)

| Endpoint | Use |
|---|---|
| `GET /api/health` | Which keys/integrations are active |
| `GET /api/kb/search?q=warranty` | Test RAG retrieval directly |
| `POST /api/kb/reingest` | Rebuild RAG index after editing `knowledge_base/` |
| `POST /api/calls/{id}/text` | Send a typed message instead of audio (skips STT) |
| `GET /api/calls/{id}` | Full transcript + analysis |

Server logs show every tool call, e.g. `[tool] update_qualification(budget=True) -> score 30 COLD`,
and each reply shows latency per stage (stt / llm / tts ms) in the transcript.

## 7. Troubleshooting

| Problem | Fix |
|---|---|
| `check_setup` Gemini FAIL "model not found" | Copy a current model ID from AI Studio into `LLM_MODEL` / `ANALYSIS_MODEL` in `.env` |
| Gemini error about `thinking_level` | Set `LLM_THINKING_LEVEL=` (empty) in `.env` |
| Agent answers without calling tools | Set `LLM_THINKING_LEVEL=medium` |
| 429 / quota errors | Free tier is rate-limited - wait a minute, or create a key in a new AI Studio project |
| Sarvam 400 about speaker | Change `TTS_SPEAKER` (e.g. `shubh`, `anushka` for v2) |
| Mic does not start | Use Chrome on `http://localhost` (mic needs localhost or https); use the "Type instead" box |
| Agent interrupts itself | Use headphones or untick "Allow interrupting the agent" |
| Reset all data | Stop server, delete `data/app.db` |

## Known MVP limitations (by design, see docs/SCALING.md for the production path)

- Turn-based pipeline (detect end of speech -> STT -> LLM -> TTS), typically ~2-4 s per reply; production uses
  streaming STT/LLM/TTS over WebRTC (LiveKit) to get under ~1 s.
- Browser call instead of a phone number (Indian telephony for automated calls needs TRAI registration).
- In-memory sessions + SQLite: single server only.
- Company, prices and policies in `knowledge_base/` are fictional sample data.
