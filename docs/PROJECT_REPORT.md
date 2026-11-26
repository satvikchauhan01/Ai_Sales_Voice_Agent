# Autonomous AI Sales Voice Agent: Project Report

**SunRise Solar · "Priya" voice agent · MVP · November 2026**

## 1. Problem and solution
Indian small businesses (solar, real estate, insurance) buy leads but call them back hours later, in office
hours only, in one language, and little reaches the CRM. This agent talks to a new lead within seconds in the
caller's language: it answers product questions from company documents, qualifies the lead, books a free
site survey and writes a summary into the CRM, so sales staff only spend time on hot leads.

## 2. How it works
Browser VAD (Silero) detects end of speech → **FastAPI** backend → **Sarvam** speech-to-text → **Gemini 3.5
Flash-Lite** with tools → **Sarvam** text-to-speech → reply played in the browser. Data lives in **SQLite**; each
finished call is synced to **HubSpot** (contact, deal, note).

| Requirement | Implementation |
|---|---|
| Real-time voice (STT + TTS) | Sarvam Saaras STT + Bulbul TTS; turn-based pipeline with live captions |
| Contextual memory | Chat history within a call; past BANT answers, summaries and bookings loaded by phone number |
| Multilingual | Language auto-detection; replies in 11 Indian languages, in native script |
| Lead qualification | LLM tool records BANT answers; Python computes a deterministic 0-100 score (COLD → HOT → BOOKED) |
| Sentiment analysis | Post-call structured JSON: sentiment score, mood trend, objections, buying signals |
| RAG | Markdown knowledge base → Gemini embeddings → cosine search, used as an LLM tool |
| Appointment booking | Tools for free slots and booking (local calendar; Cal.com optional) |
| CRM integration | Local leads table plus HubSpot contact, deal stage and AI-summary note |
| Summary + insights | Summary card after each call; dashboard KPIs, charts and AI-written business insights |

## 3. Engineering highlights
- **Tools, not free text:** Gemini decides *when* to search, score or book; Python validates every action.
- **Resilience:** timeout + retry per Gemini request, then a fallback model with the full conversation, then
  a spoken apology instead of an error; network retries for Sarvam.
- **UI:** animated voice orb that reacts to both voices, live lead-score ring, dashboard with call drawer.
- **Testing:** 15 automated tests (full sales workflow, fallback, failure handling) run without API keys.

## 4. Results (live test, 26 Nov 2026)
The full workflow ran on real APIs: greeting → price answer from the knowledge base → lead score 0 → 100 →
survey booked → summary. HubSpot sync (contact, deal stage, note) was verified separately against a real
account. A typical reply takes **about 7-12 s**
on free tiers (STT ~1-2 s, LLM ~3-6 s, TTS ~3-5 s). Debugging cut LLM time per turn from 33-38 s to 4-7 s,
mainly by fixing knowledge-base retrieval so the model searches once instead of four times.

## 5. Scaling, security and cost (detail in `SCALING.md`)
- **1 lakh users/month** ≈ 4 lakh call-minutes ≈ 150 concurrent calls at peak (design target).
- **Production:** streaming pipeline on LiveKit (<1 s replies), Exotel phone lines, stateless workers on
  Kubernetes, Redis, PostgreSQL + pgvector, queued post-call analysis, AWS Mumbai.
- **Security:** secrets in env vars, TLS, masked PII in logs, role-based login, server-side tool validation,
  AI disclosure, DPDP Act and TRAI compliance.
- **Cost:** ₹0 for the MVP (free tiers); about **₹2.4-4.4 per call-minute** at scale (₹10-18 per call).

## 6. Limitations and next steps
Turn-based replies (not streaming), browser call instead of a phone number, in-memory call sessions,
SQLite, and a fictional knowledge base. Next steps: streaming pipeline, telephony, Redis/Postgres, and a
load test with 150 simulated concurrent calls.
