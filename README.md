# Enchant Calendar Poller

Replaces the "Calendar Event Pulling" n8n workflow. Every 15 minutes, checks
for anything created or changed on the calendar since the last check — new
events get the same AI-importance judgment n8n did (and a task if flagged
important); events that already exist just get their fields refreshed.

## Reuses the same Google OAuth setup as the Gmail poller

If you already did the `get_refresh_token.py` step for the Gmail poller, you
can reuse the SAME Google Cloud project and OAuth client — you just need one
more refresh token, this time for the Calendar scope instead of Gmail's.

1. In `get_refresh_token.py` (from the Gmail poller folder), change this line:
   ```python
   SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
   ```
   to:
   ```python
   SCOPES = ["https://www.googleapis.com/auth/calendar"]
   ```
2. Run it, log into `adam@adamprather.com` (the calendar account), copy the
   printed refresh token.

## Run the Supabase migration (if not already done)

This reuses the same `sync_state` table from the Gmail poller migration —
if you already ran that one, nothing new to do here.

## Deploy

Same GitHub-upload-then-Railway-connect pattern as before.

## Environment variables

```
SUPABASE_URL=...
SUPABASE_SERVICE_KEY=...
ANTHROPIC_API_KEY=...
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
GOOGLE_CALENDAR_REFRESH_TOKEN=...     (the new one, calendar scope)
CALENDAR_ID=adam@adamprather.com
```

## Test

- `curl https://your-domain/health`
- `curl -X POST https://your-domain/run-now` — checks for new/updated events immediately
- `curl -X POST https://your-domain/backfill` — pulls the full current week, same as the manual Backfill Trigger in n8n

## What's intentionally different from the n8n version

n8n used two separate triggers (hourly for new, 15-min for updates). This
collapses both into one 15-minute check that branches on whether the event
already exists in Supabase — functionally equivalent, one fewer moving part.
