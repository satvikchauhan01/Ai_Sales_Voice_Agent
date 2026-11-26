"""End-to-end business workflow through the real API, with Sarvam + Gemini faked.

Lead created -> greeting -> qualification tool -> RAG tool -> slots + booking tools
-> post-call analysis -> dashboard metrics -> returning lead gets memory.
"""
import io

from fastapi.testclient import TestClient

from tests import fakes


def test_full_sales_workflow(monkeypatch):
    fakes.install(monkeypatch)
    from app.main import app

    with TestClient(app) as client:
        health = client.get("/api/health").json()
        assert health["calendar"] == "local"

        # RAG works on the real knowledge base files (with fake embeddings)
        hits = client.get("/api/kb/search", params={"q": "warranty"}).json()
        assert len(hits) == 3

        # 1) start call
        start = client.post("/api/calls/start", json={
            "name": "Rahul", "phone": "9876543210", "email": "rahul@example.com", "language": "auto"}).json()
        call_id = start["call_id"]
        assert "Priya" in start["agent_text"]
        assert start["audio_b64"]
        assert start["returning_lead"] is False

        wav = io.BytesIO(b"RIFF....fake")

        # 2) qualification turn -> update_qualification tool runs for real
        turn1 = client.post(f"/api/calls/{call_id}/turn", files={"audio": ("s.wav", wav, "audio/wav")}).json()
        assert turn1["lead"]["score"] == 30
        assert turn1["lead"]["bant"]["budget"]["qualified"] is True

        # 3) product question -> search_knowledge_base tool
        turn2 = client.post(f"/api/calls/{call_id}/turn", files={"audio": ("s.wav", wav, "audio/wav")}).json()
        assert "lakh" in turn2["agent_text"]

        # 4) booking -> get_available_slots + book_appointment tools (chained)
        turn3 = client.post(f"/api/calls/{call_id}/turn", files={"audio": ("s.wav", wav, "audio/wav")}).json()
        assert "booked" in turn3["agent_text"].lower()
        assert turn3["lead"]["status"] == "BOOKED"

        # text fallback path
        text_turn = client.post(f"/api/calls/{call_id}/text", json={"text": "I am the owner"}).json()
        assert text_turn["lead"]["bant"]["authority"]["qualified"] is True

        # 5) end call -> analysis + CRM sync (local only, no HubSpot token)
        result = client.post(f"/api/calls/{call_id}/end").json()
        assert result["outcome"] == "appointment_booked"  # DB truth overrides LLM
        assert result["sentiment"] == "positive"
        assert "skipped" in result["crm"]["hubspot"]

        detail = client.get(f"/api/calls/{call_id}").json()
        roles = [t["role"] for t in detail["turns"]]
        assert roles[0] == "agent" and roles.count("user") == 4

        dash = client.get("/api/dashboard").json()
        assert dash["totals"]["appointments"] >= 1
        assert dash["top_objections"][0][0] == "price too high"

        # 6) same phone calls again -> returning lead with memory in the prompt
        again = client.post("/api/calls/start", json={
            "name": "Rahul", "phone": "9876543210", "email": "", "language": "hi-IN"}).json()
        assert again["returning_lead"] is True
        assert again["language"] == "hi-IN"
        from app.agent import SESSIONS, build_memory
        from app import db
        memory = build_memory(db.get_lead(SESSIONS[again["call_id"]].lead_id))
        assert "Earlier call" in memory and "Existing booked site survey" in memory
