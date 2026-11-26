"""Tools the LLM can call during a call (function calling).

Each tool is a plain Python function with type hints + a docstring; the google-genai SDK
turns these into function declarations automatically. The functions are created per call
(closures) so they know which lead/call they belong to - the LLM never sees or chooses IDs.

Rule: tools validate their inputs and return short plain-text results. They never raise,
because an exception would end the LLM turn; instead they return an error message the
LLM can recover from ("that slot is taken, offer another").
"""
import json
import logging
from datetime import datetime

from app import booking, db, rag
from app.qualification import CRITERIA, apply_answer, score_lead

log = logging.getLogger("tools")


def build_tools(lead_id: int, call_id: int) -> list:

    def search_knowledge_base(question: str) -> str:
        """Search SunRise Solar's product documents (systems, pricing, subsidy help, warranty,
        installation, financing, FAQs). Use this before answering ANY question about the
        company or its products. Never invent prices or facts.

        Args:
            question: The caller's question, rephrased as a clear search query.
        """
        results = rag.search(question, top_k=4)
        log.info("[tool] search_knowledge_base(%r) -> %d results", question, len(results))
        if not results:
            return "No information found in the knowledge base."
        parts = []
        for r in results:
            parts.append(f"[{r['source']} > {r['heading']}]\n{r['content']}")
        return "\n\n".join(parts)

    def update_qualification(criterion: str, qualified: bool, note: str) -> str:
        """Record the caller's answer for one BANT lead-qualification criterion.
        Call this as soon as the caller reveals the information.

        Args:
            criterion: One of: budget, authority, need, timeline.
            qualified: True if the answer is positive for a sale (e.g. budget fits,
                caller is the decision maker, has a real need, wants it within 3 months).
            note: Short note of what the caller said, e.g. "monthly bill around 4000 rupees".
        """
        criterion = criterion.strip().lower()
        if criterion not in CRITERIA:
            return f"Invalid criterion '{criterion}'. Use one of: {', '.join(CRITERIA)}."

        lead = db.get_lead(lead_id)
        bant = json.loads(lead["bant_json"] or "{}")
        bant = apply_answer(bant, criterion, qualified, note)
        score, status = score_lead(bant)
        if lead["status"] == "BOOKED":
            status = "BOOKED"  # never downgrade a booked lead
        db.update_lead(lead_id, bant_json=json.dumps(bant), score=score, status=status)
        log.info("[tool] update_qualification(%s=%s) -> score %s %s", criterion, qualified, score, status)

        missing = [c for c in CRITERIA if c not in bant]
        return f"Saved. Lead score is now {score}/100 ({status}). Still unknown: {', '.join(missing) or 'nothing'}."

    def get_available_slots(date: str) -> str:
        """Get free time slots for the site survey appointment on a given date.

        Args:
            date: Date in YYYY-MM-DD format (resolve words like 'tomorrow' using today's date).
        """
        try:
            slots = booking.get_slots(date)
        except Exception as exc:
            return f"Could not fetch slots: {exc}"
        log.info("[tool] get_available_slots(%s) -> %d slots", date, len(slots))
        if not slots:
            return "No free slots on that date. Suggest another day."
        lines = [f"{s['label']} -> slot_start={s['start']}" for s in slots[:6]]
        return "Free slots:\n" + "\n".join(lines)

    def book_appointment(slot_start: str, email: str) -> str:
        """Book the site survey after the caller has CONFIRMED a specific slot.

        Args:
            slot_start: The exact slot_start value returned by get_available_slots.
            email: Caller's email for the confirmation, or empty string if not given.
        """
        lead = db.get_lead(lead_id)
        email = email.strip() or lead["email"]
        try:
            appointment = booking.book(lead, call_id, slot_start, email)
        except Exception as exc:
            log.warning("[tool] book_appointment failed: %s", exc)
            return f"Booking failed: {exc}. Offer the caller a different slot."
        if email and email != lead["email"]:
            db.update_lead(lead_id, email=email)
        db.update_lead(lead_id, status="BOOKED")
        label = booking.format_slot(datetime.fromisoformat(appointment["start_time"]))
        log.info("[tool] book_appointment -> %s (%s)", label, appointment["source"])
        return f"Booked successfully for {label}. Confirm the date and time to the caller."

    return [search_knowledge_base, update_qualification, get_available_slots, book_appointment]
