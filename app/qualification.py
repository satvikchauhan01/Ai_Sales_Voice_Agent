"""BANT lead qualification. The LLM *collects* answers; this code *scores* them.

Keeping the score deterministic makes it explainable and testable
(an LLM-generated score would change between runs).
"""
from app.config import BANT_WEIGHTS, HOT_THRESHOLD, WARM_THRESHOLD

CRITERIA = list(BANT_WEIGHTS.keys())  # budget, authority, need, timeline


def score_lead(bant: dict) -> tuple[int, str]:
    """bant looks like {"budget": {"qualified": True, "note": "..."}, ...}."""
    score = 0
    for criterion, weight in BANT_WEIGHTS.items():
        answer = bant.get(criterion)
        if answer is not None and answer.get("qualified") is True:
            score += weight

    if score >= HOT_THRESHOLD:
        status = "HOT"
    elif score >= WARM_THRESHOLD:
        status = "WARM"
    else:
        status = "COLD"
    return score, status


def apply_answer(bant: dict, criterion: str, qualified: bool, note: str) -> dict:
    """Return a new BANT dict with one criterion updated."""
    updated = dict(bant)
    updated[criterion] = {"qualified": qualified, "note": note}
    return updated
