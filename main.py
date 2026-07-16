"""
Enchant Webhooks - Fireflies meeting receiver
Thin FastAPI service: receives a Fireflies webhook notification, fetches the
full transcript/summary via Fireflies' GraphQL API, runs the same AI-importance
triage used elsewhere in this system, and writes to Supabase.
"""

import hashlib
import hmac
import os
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel

app = FastAPI(title="Enchant Webhooks")

SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
FIREFLIES_API_KEY = os.environ["FIREFLIES_API_KEY"]
FIREFLIES_WEBHOOK_SECRET = os.environ["FIREFLIES_WEBHOOK_SECRET"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]

# Which inbox/category a Fireflies meeting is tagged under on the dashboard.
# Same tagging convention as the existing email/calendar pipeline.
DEFAULT_ACCOUNT_EMAIL = os.environ.get("MEETINGS_ACCOUNT_EMAIL", "adam@adamprather.com")
DEFAULT_CATEGORY = os.environ.get("MEETINGS_CATEGORY", "executive")


class FirefliesPayload(BaseModel):
    meetingId: str
    eventType: str | None = None
    clientReferenceId: str | None = None


def verify_signature(raw_body: bytes, signature_header: str | None) -> None:
    """Fireflies signs webhook payloads with HMAC-SHA256 using your webhook secret."""
    if not signature_header:
        raise HTTPException(status_code=401, detail="Missing signature")
    expected = hmac.new(
        FIREFLIES_WEBHOOK_SECRET.encode(), raw_body, hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, signature_header):
        raise HTTPException(status_code=401, detail="Invalid signature")


async def fetch_transcript(meeting_id: str) -> dict:
    """Pull the full transcript + AI summary from Fireflies' GraphQL API."""
    query = """
    query Transcript($id: String!) {
      transcript(id: $id) {
        title
        date
        duration
        participants
        summary { overview action_items }
        sentences { text speaker_name }
      }
    }
    """
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            "https://api.fireflies.ai/graphql",
            json={"query": query, "variables": {"id": meeting_id}},
            headers={"Authorization": f"Bearer {FIREFLIES_API_KEY}"},
        )
        resp.raise_for_status()
        data = resp.json()
        if "errors" in data:
            raise HTTPException(status_code=502, detail=f"Fireflies API error: {data['errors']}")
        return data["data"]["transcript"]


async def judge_importance(title: str, summary: str, action_items: str) -> dict:
    """Same AI-triage pattern used for emails and calendar events elsewhere in
    this system: judge importance, priority, and whether a task should be
    created — kept consistent so all three sources feed Daily Brief the same way."""
    prompt = (
        "Read this meeting summary and respond with ONLY valid JSON, nothing else, "
        "in this exact shape:\n"
        '{"is_important": true or false, "priority": "high", "medium", or "low", '
        '"label": "one short word", "task_title": "a short actionable task title, '
        'or empty string if none needed", "meeting_summary": "one sentence summary"}\n\n'
        f"Title: {title}\nSummary: {summary}\nAction items: {action_items}"
    )
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            "https://api.anthropic.com/v1/messages",
            json={
                "model": "claude-sonnet-4-6",
                "max_tokens": 500,
                "messages": [{"role": "user", "content": prompt}],
            },
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
        )
        resp.raise_for_status()
        text = resp.json()["content"][0]["text"]
        text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        import json
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"is_important": False, "priority": "low", "label": "", "task_title": "", "meeting_summary": "Could not parse AI response"}


async def write_to_supabase(table: str, row: dict) -> None:
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{SUPABASE_URL}/rest/v1/{table}",
            json=row,
            headers={
                "apikey": SUPABASE_SERVICE_KEY,
                "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
                "Content-Type": "application/json",
                "Prefer": "return=minimal",
            },
        )
        if resp.status_code >= 300:
            raise HTTPException(status_code=502, detail=f"Supabase write failed: {resp.text}")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/webhooks/fireflies")
async def fireflies_webhook(request: Request, x_hub_signature: str | None = Header(default=None)):
    raw_body = await request.body()
    verify_signature(raw_body, x_hub_signature)

    payload = FirefliesPayload.model_validate_json(raw_body)

    if payload.eventType and payload.eventType != "Transcription completed":
        return {"status": "ignored", "reason": f"event type {payload.eventType} not handled"}

    transcript = await fetch_transcript(payload.meetingId)
    summary = transcript.get("summary") or {}
    overview = summary.get("overview", "")
    action_items = summary.get("action_items", "")
    attendees = ", ".join(transcript.get("participants") or [])

    triage = await judge_importance(transcript.get("title", ""), overview, action_items)

    await write_to_supabase("meetings", {
        "fireflies_meeting_id": payload.meetingId,
        "title": transcript.get("title"),
        "summary": overview,
        "action_items": action_items,
        "attendees": attendees,
        "duration_minutes": transcript.get("duration"),
        "account_email": DEFAULT_ACCOUNT_EMAIL,
        "category": DEFAULT_CATEGORY,
        "is_important": bool(triage.get("is_important")),
        "occurred_at": transcript.get("date"),
    })

    if triage.get("is_important") and triage.get("task_title"):
        await write_to_supabase("tasks", {
            "title": triage["task_title"],
            "label": triage.get("label", ""),
            "priority": triage.get("priority", "low"),
            "source": "Fireflies",
            "is_complete": False,
            "active_date": datetime.now(timezone.utc).date().isoformat(),
            "account_email": DEFAULT_ACCOUNT_EMAIL,
            "category": DEFAULT_CATEGORY,
        })

    return {"status": "ok", "meeting_id": payload.meetingId, "important": triage.get("is_important", False)}
