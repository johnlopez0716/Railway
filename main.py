"""
Enchant Gmail Poller — replaces the "Enchant Emails Workflows" n8n workflow.

Runs on a schedule (hourly, matching n8n's cadence), checks all 4 inboxes for
new mail since the last check, runs the same AI-importance triage used
elsewhere in this system, and writes to Supabase (emails + tasks tables)
using the same account_email / category tagging convention.
"""

import json
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI

SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
GOOGLE_CLIENT_ID = os.environ["GOOGLE_CLIENT_ID"]
GOOGLE_CLIENT_SECRET = os.environ["GOOGLE_CLIENT_SECRET"]

# One set of these three per inbox. Matches the 4 accounts already tagged
# this way on the dashboard.
ACCOUNTS = [
    {
        "email": os.environ["GMAIL_ACCOUNT_1_EMAIL"],
        "category": os.environ["GMAIL_ACCOUNT_1_CATEGORY"],
        "refresh_token": os.environ["GMAIL_ACCOUNT_1_REFRESH_TOKEN"],
    },
    {
        "email": os.environ["GMAIL_ACCOUNT_2_EMAIL"],
        "category": os.environ["GMAIL_ACCOUNT_2_CATEGORY"],
        "refresh_token": os.environ["GMAIL_ACCOUNT_2_REFRESH_TOKEN"],
    },
    {
        "email": os.environ["GMAIL_ACCOUNT_3_EMAIL"],
        "category": os.environ["GMAIL_ACCOUNT_3_CATEGORY"],
        "refresh_token": os.environ["GMAIL_ACCOUNT_3_REFRESH_TOKEN"],
    },
    {
        "email": os.environ["GMAIL_ACCOUNT_4_EMAIL"],
        "category": os.environ["GMAIL_ACCOUNT_4_CATEGORY"],
        "refresh_token": os.environ["GMAIL_ACCOUNT_4_REFRESH_TOKEN"],
    },
]


async def get_access_token(refresh_token: str) -> str:
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
        )
        resp.raise_for_status()
        return resp.json()["access_token"]


async def get_sync_state(account_email: str) -> str | None:
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"{SUPABASE_URL}/rest/v1/sync_state",
            params={"account_email": f"eq.{account_email}", "select": "last_synced_at"},
            headers={"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"},
        )
        resp.raise_for_status()
        rows = resp.json()
        return rows[0]["last_synced_at"] if rows else None


async def set_sync_state(account_email: str, when: str) -> None:
    async with httpx.AsyncClient(timeout=15) as client:
        await client.post(
            f"{SUPABASE_URL}/rest/v1/sync_state",
            json={"account_email": account_email, "last_synced_at": when},
            headers={
                "apikey": SUPABASE_SERVICE_KEY,
                "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
                "Content-Type": "application/json",
                "Prefer": "resolution=merge-duplicates",
            },
        )


async def list_new_messages(access_token: str, after_epoch: int) -> list[dict]:
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages",
            params={"q": f"after:{after_epoch}"},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        resp.raise_for_status()
        return resp.json().get("messages", [])


async def fetch_message(access_token: str, message_id: str) -> dict:
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{message_id}",
            params={"format": "metadata", "metadataHeaders": ["Subject", "From", "Date"]},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        resp.raise_for_status()
        data = resp.json()
        headers = {h["name"]: h["value"] for h in data.get("payload", {}).get("headers", [])}
        return {
            "id": data["id"],
            "subject": headers.get("Subject", ""),
            "from": headers.get("From", ""),
            "date": headers.get("Date", ""),
            "snippet": data.get("snippet", ""),
        }


async def judge_importance(subject: str, sender: str, snippet: str) -> dict:
    """Same AI-triage prompt shape used across email/calendar/meetings."""
    prompt = (
        "Read this email and respond with ONLY valid JSON, nothing else, in this exact shape:\n"
        '{"is_important": true or false, "priority": "high", "medium", or "low", '
        '"label": "one short word", "task_title": "a short actionable task title, '
        'or empty string if none needed", "summary": "one sentence summary"}\n\n'
        f"Subject: {subject}\nFrom: {sender}\nBody: {snippet}"
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
        if resp.status_code >= 300:
            raise RuntimeError(f"Anthropic API error {resp.status_code}: {resp.text}")
        text = resp.json()["content"][0]["text"]
        text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"is_important": False, "priority": "low", "label": "", "task_title": "", "summary": "Could not parse AI response"}


async def write_row(table: str, row: dict) -> None:
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
            print(f"Supabase write to {table} failed: {resp.text}")


async def process_account(account: dict) -> dict:
    email = account["email"]
    category = account["category"]

    last_synced = await get_sync_state(email)
    if last_synced:
        after_epoch = int(datetime.fromisoformat(last_synced).timestamp())
    else:
        after_epoch = int(datetime.now(timezone.utc).timestamp()) - 3600  # first run: last hour only

    access_token = await get_access_token(account["refresh_token"])
    messages = await list_new_messages(access_token, after_epoch)

    processed = 0
    for msg_ref in messages:
        msg = await fetch_message(access_token, msg_ref["id"])
        triage = await judge_importance(msg["subject"], msg["from"], msg["snippet"])

        await write_row("emails", {
            "sender": msg["from"],
            "subject": msg["subject"],
            "received_at": msg["date"],
            "body_text": msg["snippet"],
            "summary": triage.get("summary", ""),
            "account_email": email,
            "category": category,
        })

        if triage.get("is_important") and triage.get("task_title"):
            await write_row("tasks", {
                "title": triage["task_title"],
                "label": triage.get("label", ""),
                "priority": triage.get("priority", "low"),
                "source": "Gmail",
                "is_complete": False,
                "active_date": datetime.now(timezone.utc).date().isoformat(),
                "account_email": email,
                "category": category,
            })
        processed += 1

    await set_sync_state(email, datetime.now(timezone.utc).isoformat())
    return {"account": email, "processed": processed}


async def run_all_accounts() -> list[dict]:
    results = []
    for account in ACCOUNTS:
        try:
            results.append(await process_account(account))
        except Exception as e:
            results.append({"account": account["email"], "error": str(e)})
    return results


scheduler = AsyncIOScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler.add_job(run_all_accounts, "interval", hours=1, id="gmail_poll")
    scheduler.start()
    yield
    scheduler.shutdown()


app = FastAPI(title="Enchant Gmail Poller", lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok", "accounts": [a["email"] for a in ACCOUNTS]}


@app.post("/run-now")
async def run_now():
    """Manually trigger a poll cycle immediately, for testing without waiting an hour."""
    return await run_all_accounts()
