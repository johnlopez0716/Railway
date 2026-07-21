"""
Enchant Calendar Poller — replaces the "Calendar Event Pulling" n8n workflow.

n8n used two separate triggers (new events hourly, updated events every 15
min) plus a manual backfill branch. This collapses new+updated into one
15-minute poll (the more frequent of the two cadences): pull everything
changed since the last check, then branch on whether the event already
exists in Supabase — new events get the AI-importance judgment and a
possible task, existing events just get their fields refreshed, matching
what each n8n branch did.
"""

import json
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI

SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
GOOGLE_CLIENT_ID = os.environ["GOOGLE_CLIENT_ID"]
GOOGLE_CLIENT_SECRET = os.environ["GOOGLE_CLIENT_SECRET"]
GOOGLE_CALENDAR_REFRESH_TOKEN = os.environ["GOOGLE_CALENDAR_REFRESH_TOKEN"]
CALENDAR_ID = os.environ.get("CALENDAR_ID", "adam@adamprather.com")

SYNC_KEY = "calendar_poller"  # row in sync_state for this service


async def get_access_token() -> str:
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "refresh_token": GOOGLE_CALENDAR_REFRESH_TOKEN,
                "grant_type": "refresh_token",
            },
        )
        resp.raise_for_status()
        return resp.json()["access_token"]


async def get_last_synced() -> str | None:
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"{SUPABASE_URL}/rest/v1/sync_state",
            params={"account_email": f"eq.{SYNC_KEY}", "select": "last_synced_at"},
            headers={"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"},
        )
        resp.raise_for_status()
        rows = resp.json()
        return rows[0]["last_synced_at"] if rows else None


async def set_last_synced(when: str) -> None:
    async with httpx.AsyncClient(timeout=15) as client:
        await client.post(
            f"{SUPABASE_URL}/rest/v1/sync_state",
            json={"account_email": SYNC_KEY, "last_synced_at": when},
            headers={
                "apikey": SUPABASE_SERVICE_KEY,
                "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
                "Content-Type": "application/json",
                "Prefer": "resolution=merge-duplicates",
            },
        )


async def list_updated_events(access_token: str, updated_min: str) -> list[dict]:
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"https://www.googleapis.com/calendar/v3/calendars/{CALENDAR_ID}/events",
            params={"updatedMin": updated_min, "singleEvents": "true", "orderBy": "updated"},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        resp.raise_for_status()
        return resp.json().get("items", [])


async def list_week_events(access_token: str) -> list[dict]:
    now = datetime.now(timezone.utc)
    time_min = (now - timedelta(days=1)).isoformat()
    time_max = (now + timedelta(days=7)).isoformat()
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"https://www.googleapis.com/calendar/v3/calendars/{CALENDAR_ID}/events",
            params={"timeMin": time_min, "timeMax": time_max, "singleEvents": "true", "orderBy": "startTime"},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        resp.raise_for_status()
        return resp.json().get("items", [])


async def row_exists(google_event_id: str) -> bool:
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"{SUPABASE_URL}/rest/v1/calendar_events",
            params={"google_event_id": f"eq.{google_event_id}", "select": "id"},
            headers={"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"},
        )
        resp.raise_for_status()
        return len(resp.json()) > 0


async def judge_importance(title: str, start: str, attendees: str) -> dict:
    """Same AI-triage prompt used in the original n8n workflow."""
    prompt = (
        "Read this calendar event and respond with ONLY valid JSON, nothing else, in this exact shape:\n"
        '{"is_important": true or false, "priority": "high", "medium", or "low", '
        '"calendar_label": "one short word", "task_title": "a short actionable task title, '
        'or empty string if none needed", "event_summary": "one sentence summary"}\n\n'
        f"Title: {title}\nStart: {start}\nAttendees: {attendees}"
    )
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            "https://api.anthropic.com/v1/messages",
            json={"model": "claude-sonnet-5", "max_tokens": 500, "messages": [{"role": "user", "content": prompt}]},
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
        )
        resp.raise_for_status()
        text = resp.json()["content"][0]["text"]
        text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"is_important": False, "priority": "low", "calendar_label": "", "task_title": "", "event_summary": "Could not parse AI response"}


def event_fields(event: dict) -> dict:
    attendees = ", ".join(a.get("email", "") for a in event.get("attendees", []))
    contact_name = (event.get("attendees") or [{"email": ""}])[0].get("email", "")
    return {
        "title": event.get("summary", ""),
        "event_time": (event.get("start") or {}).get("dateTime", ""),
        "attendees": attendees,
        "contact_name": contact_name,
        "google_event_id": event["id"],
    }


async def supabase_request(method: str, table: str, **kwargs) -> httpx.Response:
    async with httpx.AsyncClient(timeout=15) as client:
        return await client.request(
            method,
            f"{SUPABASE_URL}/rest/v1/{table}",
            headers={
                "apikey": SUPABASE_SERVICE_KEY,
                "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
                "Content-Type": "application/json",
                "Prefer": kwargs.pop("prefer", "return=minimal"),
            },
            **kwargs,
        )


async def handle_new_event(event: dict) -> None:
    fields = event_fields(event)
    attendees = fields["attendees"]
    triage = await judge_importance(fields["title"], fields["event_time"], attendees)

    await supabase_request("POST", "calendar_events", json=fields)
    await supabase_request(
        "PATCH", "calendar_events",
        params={"google_event_id": f"eq.{fields['google_event_id']}"},
        json={"is_important": bool(triage.get("is_important"))},
    )

    if triage.get("is_important") and triage.get("task_title"):
        await supabase_request("POST", "tasks", json={
            "title": triage["task_title"],
            "label": triage.get("calendar_label", ""),
            "priority": triage.get("priority", "low"),
            "source": "Calendar",
            "is_complete": False,
            "active_date": datetime.now(timezone.utc).date().isoformat(),
        })


async def handle_updated_event(event: dict) -> None:
    fields = event_fields(event)
    await supabase_request(
        "PATCH", "calendar_events",
        params={"google_event_id": f"eq.{fields['google_event_id']}"},
        json={"title": fields["title"], "event_time": fields["event_time"], "attendees": fields["attendees"]},
    )


async def poll_cycle() -> dict:
    access_token = await get_access_token()
    last_synced = await get_last_synced()
    updated_min = last_synced or (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()

    events = await list_updated_events(access_token, updated_min)
    new_count = 0
    updated_count = 0
    for event in events:
        if event.get("status") == "cancelled":
            continue
        if await row_exists(event["id"]):
            await handle_updated_event(event)
            updated_count += 1
        else:
            await handle_new_event(event)
            new_count += 1

    await set_last_synced(datetime.now(timezone.utc).isoformat())
    return {"new": new_count, "updated": updated_count, "checked": len(events)}


async def run_backfill() -> dict:
    access_token = await get_access_token()
    events = await list_week_events(access_token)
    for event in events:
        if event.get("status") == "cancelled":
            continue
        fields = event_fields(event)
        await supabase_request(
            "DELETE", "calendar_events",
            params={"google_event_id": f"eq.{fields['google_event_id']}"},
        )
        await supabase_request("POST", "calendar_events", json=fields)
    return {"backfilled": len(events)}


scheduler = AsyncIOScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler.add_job(poll_cycle, "interval", minutes=15, id="calendar_poll")
    scheduler.start()
    yield
    scheduler.shutdown()


app = FastAPI(title="Enchant Calendar Poller", lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok", "calendar": CALENDAR_ID}


@app.post("/run-now")
async def run_now():
    return await poll_cycle()


@app.post("/backfill")
async def backfill():
    """Equivalent of manually clicking the Backfill Trigger in n8n."""
    return await run_backfill()
