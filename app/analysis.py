"""Post-call intelligence: summary, sentiment, objections, outcome (structured JSON from Gemini),
plus dashboard metrics and an AI-written business insight report.
"""
import json
import logging
from collections import Counter

from pydantic import BaseModel, Field

from app import db, llm

log = logging.getLogger("analysis")

SENTIMENTS = ["positive", "neutral", "negative"]
OUTCOMES = ["appointment_booked", "follow_up", "not_interested", "no_decision"]


class CallAnalysis(BaseModel):
    summary: str = Field(description="3-4 sentence summary of the call in English for the sales team")
    sentiment: str = Field(description="Overall caller sentiment: positive, neutral or negative")
    sentiment_score: float = Field(description="-1.0 (very negative) to 1.0 (very positive)")
    sentiment_trend: str = Field(description="How the mood changed, e.g. 'started sceptical, ended positive'")
    objections: list[str] = Field(description="Short objections raised, e.g. 'price too high'")
    buying_signals: list[str] = Field(description="Signals of purchase intent")
    interested_in: str = Field(description="Product/system size the caller is interested in, or 'unknown'")
    next_step: str = Field(description="Recommended next action for the sales team")
    outcome: str = Field(description="appointment_booked, follow_up, not_interested or no_decision")


def _transcript_text(call_id: int) -> str:
    lines = []
    for turn in db.get_turns(call_id):
        speaker = "Agent" if turn["role"] == "agent" else "Caller"
        lines.append(f"{speaker}: {turn['text']}")
    return "\n".join(lines)


def _normalize(result: CallAnalysis, booked: bool) -> dict:
    data = result.model_dump()
    if data["sentiment"] not in SENTIMENTS:
        data["sentiment"] = "neutral"
    if data["outcome"] not in OUTCOMES:
        data["outcome"] = "no_decision"
    if booked:
        data["outcome"] = "appointment_booked"  # ground truth from our DB beats the LLM
    data["sentiment_score"] = max(-1.0, min(1.0, float(data["sentiment_score"])))
    return data


def analyze_call(call_id: int) -> dict:
    call = db.get_call(call_id)
    turns = db.get_turns(call_id)
    user_turns = [t for t in turns if t["role"] == "user"]
    booked = any(a["call_id"] == call_id for a in db.list_appointments(lead_id=call["lead_id"]))

    if not user_turns:
        data = {"summary": "Call ended before the caller spoke.", "sentiment": "neutral", "sentiment_score": 0.0,
                "sentiment_trend": "", "objections": [], "buying_signals": [], "interested_in": "unknown",
                "next_step": "Call again later.", "outcome": "no_decision"}
    else:
        lead = db.get_lead(call["lead_id"])
        prompt = (
            "You are a sales analyst. Analyse this sales call transcript between an AI agent and a caller "
            "for a rooftop solar company. The caller may speak Hindi, English or other Indian languages; "
            "write all output in English.\n\n"
            f"Lead qualification data (BANT): {lead['bant_json']}\n"
            f"Appointment booked in this call: {booked}\n\n"
            f"TRANSCRIPT:\n{_transcript_text(call_id)}"
        )
        try:
            data = _normalize(llm.generate_json(prompt, CallAnalysis), booked)
        except Exception as exc:
            log.error("Analysis failed: %s", exc)
            data = {"summary": f"Analysis failed: {exc}", "sentiment": "neutral", "sentiment_score": 0.0,
                    "sentiment_trend": "", "objections": [], "buying_signals": [], "interested_in": "unknown",
                    "next_step": "Review transcript manually.", "outcome": "no_decision"}

    db.update_call(
        call_id,
        summary=data["summary"],
        sentiment=data["sentiment"],
        sentiment_score=data["sentiment_score"],
        outcome=data["outcome"],
        analysis_json=json.dumps(data),
    )
    return data


# ---------------- Business insights ----------------
def dashboard_metrics() -> dict:
    leads = db.list_leads()
    calls = db.list_calls(limit=1000)
    finished = [c for c in calls if c["ended_at"]]
    appointments = db.list_appointments()

    status_counts = Counter(lead["status"] for lead in leads)
    sentiment_counts = Counter(c["sentiment"] for c in finished if c["sentiment"])
    language_counts = Counter(c["language"] for c in calls if c["language"])
    outcome_counts = Counter(c["outcome"] for c in finished if c["outcome"])

    objection_counter: Counter = Counter()
    for call in finished:
        analysis = json.loads(call["analysis_json"] or "{}")
        for objection in analysis.get("objections", []):
            objection_counter[objection.strip().lower()] += 1

    total_duration = sum(c["duration_sec"] or 0 for c in finished)
    if finished:
        avg_duration = round(total_duration / len(finished))
        avg_sentiment = round(sum(c["sentiment_score"] or 0 for c in finished) / len(finished), 2)
        booking_rate = round(100 * outcome_counts.get("appointment_booked", 0) / len(finished), 1)
    else:
        avg_duration = 0
        avg_sentiment = 0
        booking_rate = 0

    return {
        "totals": {
            "leads": len(leads),
            "calls": len(calls),
            "appointments": len(appointments),
            "hot_leads": status_counts.get("HOT", 0) + status_counts.get("BOOKED", 0),
            "booking_rate_pct": booking_rate,
            "avg_call_sec": avg_duration,
            "avg_sentiment": avg_sentiment,
        },
        "lead_status": dict(status_counts),
        "sentiment": dict(sentiment_counts),
        "languages": dict(language_counts),
        "outcomes": dict(outcome_counts),
        "top_objections": objection_counter.most_common(5),
        "leads": leads,
        "calls": calls[:50],
        "appointments": appointments,
    }


def business_insights() -> str:
    calls = [c for c in db.list_calls(limit=30) if c["summary"]]
    if not calls:
        return "No analysed calls yet. Make a few calls first."
    lines = []
    for c in calls:
        lines.append(f"- outcome={c['outcome']}, sentiment={c['sentiment']}, language={c['language']}: {c['summary']}")
    prompt = (
        "You are a sales operations analyst. Based on these AI sales call summaries for a rooftop solar "
        "company, give 4 short, specific, actionable business insights (one line each): what is working, "
        "the most common objections and how to handle them, and what to change in the sales script. "
        "Plain text, numbered.\n\n" + "\n".join(lines)
    )
    return llm.generate_text(prompt)
