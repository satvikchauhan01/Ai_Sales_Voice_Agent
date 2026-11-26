"""Unit tests for the pure business logic (no network)."""
from datetime import datetime, timedelta

from app import booking, db
from app.agent import choose_language
from app.qualification import apply_answer, score_lead
from app.rag import chunk_markdown


def test_score_empty_lead_is_cold():
    assert score_lead({}) == (0, "COLD")


def test_score_hot_lead():
    bant = {}
    bant = apply_answer(bant, "need", True, "bill 5000")
    bant = apply_answer(bant, "budget", True, "ok with EMI")
    bant = apply_answer(bant, "authority", True, "owner")
    assert score_lead(bant) == (80, "HOT")


def test_score_negative_answers_do_not_count():
    bant = {"need": {"qualified": True, "note": ""}, "budget": {"qualified": False, "note": "no money"}}
    assert score_lead(bant) == (30, "COLD")


def test_score_warm_lead():
    bant = {"need": {"qualified": True, "note": ""}, "timeline": {"qualified": True, "note": ""}}
    assert score_lead(bant) == (50, "WARM")


def test_language_locked_never_switches():
    assert choose_language("hi-IN", True, "en-IN", "please tell me the price", 3) == "hi-IN"


def test_language_first_turn_follows_detection():
    assert choose_language("en-IN", False, "hi-IN", "haan", 1) == "hi-IN"


def test_language_short_utterance_does_not_switch_later():
    assert choose_language("hi-IN", False, "en-IN", "okay", 4) == "hi-IN"


def test_language_long_utterance_switches_later():
    assert choose_language("hi-IN", False, "en-IN", "can you explain the warranty please", 4) == "en-IN"


def test_language_unsupported_detection_is_ignored():
    assert choose_language("en-IN", False, "ur-IN", "some urdu sentence here", 1) == "en-IN"


def test_chunk_markdown_splits_on_headings():
    text = "# Title\nintro line\n\n## Pricing\n5 kW costs X\n\n## Warranty\n25 years"
    chunks = chunk_markdown(text, "doc.md")
    headings = [c["heading"] for c in chunks]
    assert headings == ["Title", "Pricing", "Warranty"]


def _next_working_day() -> str:
    day = datetime.now(booking.IST).date() + timedelta(days=1)
    while day.weekday() == 6:
        day += timedelta(days=1)
    return day.isoformat()


def test_local_slots_exclude_sunday_and_booked():
    db.init_db()
    day = _next_working_day()
    slots = booking.get_slots(day)
    assert 1 <= len(slots) <= 8  # 10:00 .. 17:00, minus anything booked by other tests

    lead = db.upsert_lead("Test", "9000000001", "", "auto")
    call_id = db.create_call(lead["id"], "en-IN")
    booking.book(lead, call_id, slots[0]["start"], "")
    remaining = booking.get_slots(day)
    assert len(remaining) == len(slots) - 1
    assert slots[0]["start"] not in [s["start"] for s in remaining]

    # find a Sunday
    sunday = datetime.now(booking.IST).date() + timedelta(days=1)
    while sunday.weekday() != 6:
        sunday += timedelta(days=1)
    assert booking.get_slots(sunday.isoformat()) == []


def test_cannot_double_book():
    db.init_db()
    day = _next_working_day()
    lead = db.upsert_lead("Test2", "9000000002", "", "auto")
    call_id = db.create_call(lead["id"], "en-IN")
    slot = booking.get_slots(day)[-1]["start"]
    booking.book(lead, call_id, slot, "")
    try:
        booking.book(lead, call_id, slot, "")
        assert False, "second booking should fail"
    except booking.BookingError:
        pass
