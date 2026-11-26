"""CRM integration.

The local SQLite `leads` table is always the source of truth (our mini CRM: status
NEW -> COLD/WARM/HOT -> BOOKED). If HUBSPOT_ACCESS_TOKEN is set (free HubSpot account,
private/legacy app token with contacts + deals scopes), each finished call is pushed to
HubSpot: contact upsert, deal create/update, and a note with the AI summary.
"""
import logging
from datetime import datetime, timezone

import httpx

from app import db
from app.config import HUBSPOT_ACCESS_TOKEN, hubspot_enabled

log = logging.getLogger("crm")
HUBSPOT_URL = "https://api.hubapi.com"


def _headers() -> dict:
    return {"Authorization": f"Bearer {HUBSPOT_ACCESS_TOKEN}", "Content-Type": "application/json"}


def _post(path: str, body: dict) -> dict:
    response = httpx.post(f"{HUBSPOT_URL}{path}", json=body, headers=_headers(), timeout=15)
    if response.status_code >= 300:
        raise RuntimeError(f"HubSpot {path} failed ({response.status_code}): {response.text[:200]}")
    return response.json()


def _associate(from_type: str, from_id: str, to_type: str, to_id: str) -> None:
    # v4 "default association" endpoint: no association type IDs needed.
    path = f"/crm/v4/objects/{from_type}/{from_id}/associations/default/{to_type}/{to_id}"
    response = httpx.put(f"{HUBSPOT_URL}{path}", headers=_headers(), timeout=15)
    if response.status_code >= 300:
        log.warning("HubSpot association failed: %s", response.text[:200])


def _find_contact(lead: dict) -> str:
    filters = []
    if lead["email"]:
        filters.append({"filters": [{"propertyName": "email", "operator": "EQ", "value": lead["email"]}]})
    filters.append({"filters": [{"propertyName": "phone", "operator": "EQ", "value": lead["phone"]}]})
    result = _post("/crm/v3/objects/contacts/search", {"filterGroups": filters, "limit": 1})
    items = result.get("results") or []
    if items:
        return items[0]["id"]
    return ""


def _upsert_contact(lead: dict) -> str:
    if lead["hubspot_contact_id"]:
        return lead["hubspot_contact_id"]
    contact_id = _find_contact(lead)
    if not contact_id:
        properties = {"firstname": lead["name"], "phone": lead["phone"]}
        if lead["email"]:
            properties["email"] = lead["email"]
        contact_id = _post("/crm/v3/objects/contacts", {"properties": properties})["id"]
    db.update_lead(lead["id"], hubspot_contact_id=contact_id)
    return contact_id


def _deal_stage(lead_status: str) -> str:
    # IDs of HubSpot's default sales pipeline stages.
    if lead_status == "BOOKED":
        return "appointmentscheduled"
    elif lead_status == "HOT":
        return "qualifiedtobuy"
    else:
        return ""


def sync_call(lead_id: int, call_id: int, summary: str) -> dict:
    if not hubspot_enabled():
        return {"hubspot": "skipped (no token) - lead saved in local CRM"}

    lead = db.get_lead(lead_id)
    try:
        contact_id = _upsert_contact(lead)
        result = {"hubspot_contact_id": contact_id}

        stage = _deal_stage(lead["status"])
        if stage:
            if lead["hubspot_deal_id"]:
                httpx.patch(
                    f"{HUBSPOT_URL}/crm/v3/objects/deals/{lead['hubspot_deal_id']}",
                    json={"properties": {"dealstage": stage}},
                    headers=_headers(),
                    timeout=15,
                )
                result["hubspot_deal_id"] = lead["hubspot_deal_id"]
            else:
                deal = _post(
                    "/crm/v3/objects/deals",
                    {"properties": {"dealname": f"{lead['name']} - Rooftop solar", "pipeline": "default", "dealstage": stage}},
                )
                _associate("deal", deal["id"], "contact", contact_id)
                db.update_lead(lead_id, hubspot_deal_id=deal["id"])
                result["hubspot_deal_id"] = deal["id"]

        note_body = f"AI call #{call_id} | Lead status: {lead['status']} (score {lead['score']})\n{summary}"
        note = _post(
            "/crm/v3/objects/notes",
            {"properties": {"hs_timestamp": datetime.now(timezone.utc).isoformat(), "hs_note_body": note_body}},
        )
        _associate("note", note["id"], "contact", contact_id)
        result["hubspot_note_id"] = note["id"]
        return result
    except Exception as exc:  # CRM failure must never break the call flow
        log.error("HubSpot sync failed: %s", exc)
        return {"hubspot_error": str(exc)}
