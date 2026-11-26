"""SQLite storage: leads (mini CRM), calls, transcript turns, appointments, knowledge base.

SQLite keeps the MVP zero-setup. For scale, the same tables move to PostgreSQL + pgvector
(see docs/SCALING.md).
"""
import json
import sqlite3
from datetime import datetime, timezone

from app.config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    phone TEXT NOT NULL UNIQUE,
    email TEXT DEFAULT '',
    preferred_language TEXT DEFAULT 'auto',
    bant_json TEXT DEFAULT '{}',
    score INTEGER DEFAULT 0,
    status TEXT DEFAULT 'NEW',              -- NEW, COLD, WARM, HOT, BOOKED
    hubspot_contact_id TEXT DEFAULT '',
    hubspot_deal_id TEXT DEFAULT '',
    created_at TEXT,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id INTEGER NOT NULL REFERENCES leads(id),
    language TEXT,
    started_at TEXT,
    ended_at TEXT,
    duration_sec INTEGER DEFAULT 0,
    summary TEXT DEFAULT '',
    sentiment TEXT DEFAULT '',
    sentiment_score REAL DEFAULT 0,
    outcome TEXT DEFAULT '',
    analysis_json TEXT DEFAULT '{}',
    crm_sync_json TEXT DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id INTEGER NOT NULL REFERENCES calls(id),
    role TEXT NOT NULL,                     -- user | agent
    text TEXT NOT NULL,
    language TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS appointments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id INTEGER NOT NULL REFERENCES leads(id),
    call_id INTEGER REFERENCES calls(id),
    start_time TEXT NOT NULL,               -- ISO 8601 with +05:30 offset
    source TEXT NOT NULL,                   -- local | cal.com
    external_id TEXT DEFAULT '',
    status TEXT DEFAULT 'booked',
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS kb_chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT,
    heading TEXT,
    content TEXT,
    embedding_json TEXT DEFAULT ''          -- empty when embeddings were unavailable
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(SCHEMA)


def _row_to_dict(row) -> dict | None:
    if row is None:
        return None
    return dict(row)


# ---------------- Leads ----------------
def upsert_lead(name: str, phone: str, email: str, language: str) -> dict:
    """Find lead by phone (our identity key) or create it. Returns the lead row."""
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM leads WHERE phone = ?", (phone,)).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO leads (name, phone, email, preferred_language, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (name, phone, email, language, now_iso(), now_iso()),
            )
        else:
            # Keep existing data, but refresh details the caller just gave us.
            new_email = email if email else row["email"]
            conn.execute(
                "UPDATE leads SET name = ?, email = ?, preferred_language = ?, updated_at = ? WHERE id = ?",
                (name, new_email, language, now_iso(), row["id"]),
            )
        row = conn.execute("SELECT * FROM leads WHERE phone = ?", (phone,)).fetchone()
    return _row_to_dict(row)


def get_lead(lead_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
    return _row_to_dict(row)


def update_lead(lead_id: int, **fields) -> None:
    if not fields:
        return
    fields["updated_at"] = now_iso()
    columns = ", ".join(f"{key} = ?" for key in fields)
    values = list(fields.values()) + [lead_id]
    with get_conn() as conn:
        conn.execute(f"UPDATE leads SET {columns} WHERE id = ?", values)


def list_leads() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM leads ORDER BY updated_at DESC").fetchall()
    return [dict(r) for r in rows]


# ---------------- Calls & turns ----------------
def create_call(lead_id: int, language: str) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO calls (lead_id, language, started_at) VALUES (?, ?, ?)",
            (lead_id, language, now_iso()),
        )
        return cur.lastrowid


def update_call(call_id: int, **fields) -> None:
    if not fields:
        return
    columns = ", ".join(f"{key} = ?" for key in fields)
    values = list(fields.values()) + [call_id]
    with get_conn() as conn:
        conn.execute(f"UPDATE calls SET {columns} WHERE id = ?", values)


def get_call(call_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT calls.*, leads.name AS lead_name, leads.phone AS lead_phone "
            "FROM calls JOIN leads ON leads.id = calls.lead_id WHERE calls.id = ?",
            (call_id,),
        ).fetchone()
    return _row_to_dict(row)


def list_calls(lead_id: int | None = None, limit: int = 100) -> list[dict]:
    query = (
        "SELECT calls.*, leads.name AS lead_name FROM calls "
        "JOIN leads ON leads.id = calls.lead_id "
    )
    params: list = []
    if lead_id is not None:
        query += "WHERE calls.lead_id = ? "
        params.append(lead_id)
    query += "ORDER BY calls.id DESC LIMIT ?"
    params.append(limit)
    with get_conn() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def add_turn(call_id: int, role: str, text: str, language: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO turns (call_id, role, text, language, created_at) VALUES (?, ?, ?, ?, ?)",
            (call_id, role, text, language, now_iso()),
        )


def get_turns(call_id: int) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT role, text, language, created_at FROM turns WHERE call_id = ? ORDER BY id",
            (call_id,),
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------- Appointments ----------------
def create_appointment(lead_id: int, call_id: int, start_time: str, source: str, external_id: str) -> dict:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO appointments (lead_id, call_id, start_time, source, external_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (lead_id, call_id, start_time, source, external_id, now_iso()),
        )
        row = conn.execute("SELECT * FROM appointments WHERE id = ?", (cur.lastrowid,)).fetchone()
    return dict(row)


def list_appointments(lead_id: int | None = None) -> list[dict]:
    query = (
        "SELECT appointments.*, leads.name AS lead_name FROM appointments "
        "JOIN leads ON leads.id = appointments.lead_id WHERE appointments.status = 'booked' "
    )
    params: list = []
    if lead_id is not None:
        query += "AND appointments.lead_id = ? "
        params.append(lead_id)
    query += "ORDER BY start_time"
    with get_conn() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def booked_start_times() -> set[str]:
    with get_conn() as conn:
        rows = conn.execute("SELECT start_time FROM appointments WHERE status = 'booked'").fetchall()
    return {r["start_time"] for r in rows}


# ---------------- Knowledge base ----------------
def replace_kb_chunks(chunks: list[dict]) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM kb_chunks")
        for chunk in chunks:
            embedding = chunk.get("embedding")
            embedding_json = json.dumps(embedding) if embedding else ""
            conn.execute(
                "INSERT INTO kb_chunks (source, heading, content, embedding_json) VALUES (?, ?, ?, ?)",
                (chunk["source"], chunk["heading"], chunk["content"], embedding_json),
            )


def all_kb_chunks() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM kb_chunks").fetchall()
    return [dict(r) for r in rows]
