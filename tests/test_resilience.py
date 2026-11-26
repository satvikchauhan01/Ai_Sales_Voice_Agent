"""Gemini overload handling (free tier often returns 503/504):
1) the call switches to LLM_FALLBACK_MODEL and keeps its conversation memory;
2) if every model fails, the agent says a short apology instead of the API returning a 502.
"""
from google.genai import errors, models

from app import agent, db
from app import config as app_config
from tests import fakes

db.init_db()  # tables must exist even when this file runs on its own


def _overloaded():
    return errors.ServerError(503, {"error": {"code": 503, "message": "overloaded", "status": "UNAVAILABLE"}})


def test_overloaded_model_switches_call_to_fallback(monkeypatch):
    fakes.install(monkeypatch)
    used_models = []

    def main_model_down(self, *, model, contents, config=None):
        used_models.append(model)
        if model == app_config.LLM_MODEL:
            raise _overloaded()
        return fakes.fake_generate(self, model=model, contents=contents, config=config)

    monkeypatch.setattr(models.Models, "_generate_content_with_continuation", main_model_down)

    start = agent.start_call("Asha", "9000011111", "", "en-IN")
    assert "Priya" in start["agent_text"]  # greeting came from the fallback model
    session = agent.SESSIONS[start["call_id"]]
    assert session.model == app_config.LLM_FALLBACK_MODEL

    used_models.clear()
    turn = agent.handle_text_turn(start["call_id"], "My electricity bill is about 4000 rupees")
    assert turn["lead"]["score"] == 30  # tool calling still works on the fallback model
    assert used_models and set(used_models) == {app_config.LLM_FALLBACK_MODEL}  # main model not retried
    history_text = " ".join(p.text or "" for c in session.chat.get_history() for p in (c.parts or []))
    assert "CALL CONNECTED" in history_text  # greeting turn carried over = memory kept


def test_all_models_down_gives_spoken_apology_not_error(monkeypatch):
    fakes.install(monkeypatch)

    def everything_down(self, *, model, contents, config=None):
        raise _overloaded()

    monkeypatch.setattr(models.Models, "_generate_content_with_continuation", everything_down)

    start = agent.start_call("Ravi", "9000022222", "", "hi-IN")
    assert "Ravi" in start["agent_text"] and start["audio_b64"]  # fixed greeting, call still starts

    turn = agent.handle_text_turn(start["call_id"], "आपके 5 kW सिस्टम की कीमत क्या है?")
    assert turn["agent_text"] == agent.SORRY["hi-IN"]
    assert turn["audio_b64"]
