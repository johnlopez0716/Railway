"""
Enchant Calendar Sync — replaces the "Calendar Delete Sync" n8n workflow.

Two webhook routes, matching the two Supabase Database Webhooks already
configured (just repoint their URLs here once this is deployed):
- DELETE on calendar_events -> deletes the matching Google Calendar event
- UPDATE on calendar_events -> pushes title/time changes to Google Calendar

Supabase Database Webhooks POST the raw payload directly (type, table,
record, old_record) - no extra wrapper needed, unlike n8n's webhook node
which nests everything under "body".
"""

import os

import httpx
from fastapi import FastAPI, Request

GOOGLE_CLIENT_ID = os.environ["GOOGLE_CLIENT_ID"]
GOOGLE_CLIENT_SECRET = os.environ["GOOGLE_CLIENT_SECRET"]
GOOGLE_CALENDAR_REFRESH_TOKEN = os.environ["GOOGLE_CALENDAR_REFRESH_TOKEN"]
CALENDAR_ID = os.environ.get("CALENDAR_ID", "adam@adamprather.com")

app = FastAPI(title="Enchant Calendar Sync")


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


@app.get("/health")
async def health():
    return {"status": "ok", "calendar": CALENDAR_ID}


@app.post("/webhooks/calendar-delete-sync")
async def calendar_delete_sync(request: Request):
    payload = await request.json()
    old_record = payload.get("old_record") or {}
    google_event_id = old_record.get("google_event_id")

    if not google_event_id:
        return {"status": "skipped", "reason": "no google_event_id on old_record"}

    access_token = await get_access_token()
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.delete(
            f"https://www.googleapis.com/calendar/v3/calendars/{CALENDAR_ID}/events/{google_event_id}",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        # Google returns 410 Gone if already deleted - treat as success either way
        if resp.status_code not in (200, 204, 404, 410):
            return {"status": "error", "detail": resp.text}

    return {"status": "ok", "deleted": google_event_id}


@app.post("/webhooks/calendar-edit-sync")
async def calendar_edit_sync(request: Request):
    payload = await request.json()
    record = payload.get("record") or {}
    google_event_id = record.get("google_event_id")

    if not google_event_id:
        return {"status": "skipped", "reason": "no google_event_id on record"}

    access_token = await get_access_token()
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.patch(
            f"https://www.googleapis.com/calendar/v3/calendars/{CALENDAR_ID}/events/{google_event_id}",
            json={
                "summary": record.get("title"),
                "start": {"dateTime": record.get("event_time")},
            },
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if resp.status_code >= 300:
            return {"status": "error", "detail": resp.text}

    return {"status": "ok", "updated": google_event_id}
