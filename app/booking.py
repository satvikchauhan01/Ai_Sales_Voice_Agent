"""Appointment booking.

- If CAL_API_KEY + CAL_EVENT_TYPE_ID are set -> real Cal.com calendar (free account).
- Otherwise -> built-in local calendar (Mon-Sat, 10:00-17:00 IST, hourly slots).
Both return the same shape, so the agent code does not care which one is active.
"""
import logging
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx

from app import db
from app.config import CAL_API_KEY, CAL_EVENT_TYPE_ID, TIMEZONE, calcom_enabled

log = logging.getLogger("booking")
IST = ZoneInfo(TIMEZONE)
CAL_BASE_URL = "https://api.cal.com/v2"
CAL_SLOTS_VERSION = "2024-09-04"
CAL_BOOKINGS_VERSION = "2026-02-25"
LOCAL_START_HOUR = 10
LOCAL_END_HOUR = 17  # last slot starts at 17:00


class BookingError(Exception):
    pass


def format_slot(start: datetime) -> str:
    """Human-friendly label the agent can read aloud, e.g. 'Thu 27 Nov, 11:00 AM'."""
    return start.astimezone(IST).strftime("%a %d %b, %I:%M %p")


def _parse_date(date_str: str) -> date:
    try:
        return date.fromisoformat(date_str.strip())
    except ValueError as exc:
        raise BookingError("Date must be in YYYY-MM-DD format") from exc


def _local_slots(day: date) -> list[datetime]:
    if day.weekday() == 6:  # Sunday closed
        return []
    now = datetime.now(IST)
    booked = db.booked_start_times()
    slots = []
    for hour in range(LOCAL_START_HOUR, LOCAL_END_HOUR + 1):
        start = datetime(day.year, day.month, day.day, hour, 0, tzinfo=IST)
        if start <= now + timedelta(hours=1):
            continue
        if start.isoformat() in booked:
            continue
        slots.append(start)
    return slots


def _cal_headers(version: str) -> dict:
    return {"Authorization": f"Bearer {CAL_API_KEY}", "cal-api-version": version}


def _calcom_slots(day: date) -> list[datetime]:
    params = {
        "eventTypeId": CAL_EVENT_TYPE_ID,
        "start": day.isoformat(),
        "end": day.isoformat(),
        "timeZone": TIMEZONE,
    }
    response = httpx.get(f"{CAL_BASE_URL}/slots", params=params, headers=_cal_headers(CAL_SLOTS_VERSION), timeout=15)
    if response.status_code != 200:
        raise BookingError(f"Cal.com slots failed ({response.status_code}): {response.text[:200]}")
    data = response.json().get("data") or {}
    slots = []
    for items in data.values():
        for item in items:
            start_value = item["start"] if isinstance(item, dict) else item
            slots.append(datetime.fromisoformat(start_value.replace("Z", "+00:00")).astimezone(IST))
    return slots


def get_slots(date_str: str) -> list[dict]:
    day = _parse_date(date_str)
    if day < datetime.now(IST).date():
        return []
    if calcom_enabled():
        starts = _calcom_slots(day)
    else:
        starts = _local_slots(day)
    return [{"start": s.isoformat(), "label": format_slot(s)} for s in starts]


def book(lead: dict, call_id: int, slot_start: str, email: str) -> dict:
    """Book a slot after re-checking it is still free. Returns the appointment row."""
    try:
        start = datetime.fromisoformat(slot_start.strip())
    except ValueError as exc:
        raise BookingError("slot_start must be an ISO datetime from get_available_slots") from exc
    if start.tzinfo is None:
        start = start.replace(tzinfo=IST)  # treat times without offset as IST
    else:
        start = start.astimezone(IST)

    available = {s["start"] for s in get_slots(start.date().isoformat())}
    if start.isoformat() not in available:
        raise BookingError("That slot is not available any more")

    if calcom_enabled():
        body = {
            "start": start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "eventTypeId": int(CAL_EVENT_TYPE_ID),
            "attendee": {"name": lead["name"], "timeZone": TIMEZONE},
        }
        if email:
            body["attendee"]["email"] = email
        response = httpx.post(
            f"{CAL_BASE_URL}/bookings", json=body, headers=_cal_headers(CAL_BOOKINGS_VERSION), timeout=15
        )
        if response.status_code not in (200, 201):
            raise BookingError(f"Cal.com booking failed ({response.status_code}): {response.text[:200]}")
        external_id = (response.json().get("data") or {}).get("uid", "")
        source = "cal.com"
    else:
        external_id = ""
        source = "local"

    return db.create_appointment(lead["id"], call_id, start.isoformat(), source, external_id)
