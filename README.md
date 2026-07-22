# Enchant Calendar Sync

Replaces the "Calendar Delete Sync" n8n workflow (both webhook paths).
Reuses the same Google Calendar OAuth token as `enchant-calendar` — no new
OAuth step needed.

## Deploy (same pattern as before, using a new branch)

1. In your local Railway folder, create a subfolder: `Calendar-Sync`
2. Save these 4 files into it: main.py, requirements.txt, Procfile, README.md
3. In GitHub, create a new branch called `CalendarSync` and upload these
   files there (same "Add file -> Upload files" method, just make sure
   you're on the new branch first — use the branch switcher dropdown,
   "Find or create a branch", type CalendarSync, create it, then upload).
4. In Railway, add a new service -> GitHub Repo -> same repo -> set its
   branch to `CalendarSync` and Root Directory to `/`.

## Environment variables (reuses the same 3 from enchant-calendar)

```
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
GOOGLE_CALENDAR_REFRESH_TOKEN=...
CALENDAR_ID=adam@adamprather.com
```

## Test locally first (recommended, same pattern as calendar poller)

```bash
pip install -r requirements.txt
$env:GOOGLE_CLIENT_ID="..."
$env:GOOGLE_CLIENT_SECRET="..."
$env:GOOGLE_CALENDAR_REFRESH_TOKEN="..."
$env:CALENDAR_ID="adam@adamprather.com"
python -m uvicorn main:app --reload
```

Then test with a fake payload matching what Supabase actually sends:

```powershell
Invoke-RestMethod -Uri "http://localhost:8000/health"

# Simulates a delete - replace with a REAL google_event_id from calendar_events to test for real
Invoke-RestMethod -Uri "http://localhost:8000/webhooks/calendar-delete-sync" -Method POST -Body '{"old_record":{"google_event_id":"PASTE_A_REAL_ONE_HERE"}}' -ContentType "application/json"
```

## Once deployed and tested, repoint the actual Supabase webhooks

In Supabase -> Database -> Webhooks, edit the URLs on the existing
`calendar-delete-sync` and `calendar-edit-sync` webhooks (the ones currently
pointing at n8n) to point at this new Railway service instead:

- `https://your-new-domain/webhooks/calendar-delete-sync`
- `https://your-new-domain/webhooks/calendar-edit-sync`

This is the actual cutover moment — until you do this, n8n's (now-disabled)
webhook URLs are still what Supabase tries to call, meaning deletes/edits
are NOT currently syncing to Google Calendar at all.
