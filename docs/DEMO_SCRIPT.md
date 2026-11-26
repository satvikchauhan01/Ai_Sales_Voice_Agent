# Demo Script and Live-Coding Prep

## Before the evaluation (15 min)

1. `python -m scripts.check_setup` -> all OK (fix model names in `.env` if needed).
2. Delete `data/app.db` for a clean dashboard, start `uvicorn app.main:app --reload`.
3. Headphones on. Chrome. Do one rehearsal call.
4. Backup: screen-record one complete good call (in case Wi-Fi/API fails during the demo).

## Presentation flow (~10 min)

1. **Business problem (1 min)**: Indian SMBs (solar, real estate, EdTech, insurance) buy leads but call
   them hours later; telecallers are inconsistent, 9-to-6 only, one language, and calls are not recorded
   in the CRM. Speed-to-lead and qualification are lost.
2. **Solution + architecture (2 min)**: `docs/ARCHITECTURE.md` diagram; one turn = VAD -> STT -> LLM +
   tools -> TTS.
3. **Live end-to-end workflow (4 min)** - the one business workflow:
   - Lead form -> call starts -> AI greets, discloses it is an AI.
   - Ask about price -> RAG answer (show `[tool] search_knowledge_base` in server log).
   - Give bill/ownership/timeline (switch to Hindi midway) -> BANT chips turn green, score rises.
   - Ask to book -> slots offered -> confirm -> BOOKED.
   - End call -> summary, sentiment, objections, CRM sync -> dashboard.
   - Call again with the same phone -> agent remembers the previous call.
4. **Scaling, security, cost (2 min)**: `docs/SCALING.md` tables.
5. **Limitations + next steps (1 min)**: turn-based latency -> streaming (LiveKit); browser -> telephony.

## Likely live-coding requests and where to change

| Request | Change |
|---|---|
| Change qualification weights / thresholds | `BANT_WEIGHTS`, `HOT_THRESHOLD` in `app/config.py` (tests in `tests/test_core.py`) |
| Sell a different product | `BUSINESS` in `app/config.py` + files in `knowledge_base/` + `POST /api/kb/reingest` |
| Change voice | `TTS_SPEAKER` in `.env` (e.g. `shubh`, `rahul`, `neha`) |
| Force Hindi only | Pick Hindi in the form's language dropdown (locks the language for the call) |
| Greet in Hindi when language is auto-detect | `DEFAULT_LANGUAGE = "hi-IN"` in `app/config.py` |
| Agent talks too much | Rule 1 in `build_system_prompt` (`app/agent.py`) |
| End of speech detected too early/late | `redemptionMs` in `VAD_SETTINGS` (`static/call.js`) |
| Add a new tool (e.g. send brochure) | Add a function in `build_tools` (`app/tools.py`) and include it in the returned list - see below |
| New field in call analysis (e.g. "competitor mentioned") | Add a field to `CallAnalysis` in `app/analysis.py` |
| Debug slow replies | Transcript meta shows `stt/llm/tts` ms; server log line `latency {...}` |
| Debug wrong answer | `GET /api/kb/search?q=...` to see what RAG retrieved |

New tool template (type hints + docstring are what Gemini reads):

```python
    def send_brochure(email: str) -> str:
        """Email the product brochure to the caller when they ask for details in writing.

        Args:
            email: Caller's email address.
        """
        if "@" not in email:
            return "That email looks invalid. Ask the caller to repeat it."
        db.update_lead(lead_id, email=email)
        return f"Brochure will be sent to {email}."

    return [search_knowledge_base, update_qualification, get_available_slots, book_appointment, send_brochure]
```

## Interview questions to prepare (hints only)

- Why cascaded STT/LLM/TTS vs speech-to-speech? -> transcripts, control, per-language providers, debuggability vs latency.
- How does the agent decide to call a tool? -> function declarations from signature + docstring; automatic function calling loop.
- Why is the score not computed by the LLM? -> determinism, explainability, testing.
- How does memory work? -> short-term chat history vs long-term DB summaries injected into the prompt.
- What happens if two turns arrive together? -> per-session lock, "busy" response.
- What breaks first at scale? -> provider rate limits, in-memory sessions, SQLite writes, turn latency.
