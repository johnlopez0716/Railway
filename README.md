# Enchant Gmail Poller

Replaces the "Enchant Emails Workflows" n8n workflow. Polls all 4 inboxes
hourly (same cadence as n8n), runs the same AI-importance triage, writes to
the same Supabase tables with the same account_email/category tagging.

## Step 1 — One-time Google Cloud setup (covers all 4 accounts)

1. Go to console.cloud.google.com, create a project (or use an existing one).
2. APIs & Services -> Library -> enable "Gmail API".
3. APIs & Services -> Credentials -> Create Credentials -> OAuth client ID.
   - Application type: Desktop app
   - Download the JSON, save as `credentials.json` next to `get_refresh_token.py`.
4. APIs & Services -> OAuth consent screen -> add all 4 Gmail addresses as
   "test users" (required while the app isn't verified by Google).

## Step 2 — Get a refresh token for each of the 4 inboxes

On your own computer (not Railway):

```bash
pip install google-auth-oauthlib
python get_refresh_token.py
```

This opens a browser. Log into the FIRST Gmail account, approve access, and
the script prints a refresh token. Copy it somewhere safe.

**Repeat 3 more times** — once per remaining inbox. Use an incognito/private
browser window each time so it doesn't reuse your last login. You'll end up
with 4 refresh tokens total.

## Step 3 — Run the Supabase migration

In Supabase's SQL editor, run `supabase_migration.sql` — adds the
`sync_state` table this service uses to track "checked since" per inbox.

## Step 4 — Deploy (same GitHub + Railway pattern as before)

1. Create a new GitHub repo (e.g. `enchant-gmail`), upload these files.
2. In Railway, new service -> GitHub Repository -> pick that repo.

## Step 5 — Set environment variables in Railway

```
SUPABASE_URL=...
SUPABASE_SERVICE_KEY=...
ANTHROPIC_API_KEY=...
GOOGLE_CLIENT_ID=...          (from the OAuth client you created)
GOOGLE_CLIENT_SECRET=...      (from the same OAuth client)

GMAIL_ACCOUNT_1_EMAIL=adam.prather@enchantaz.com
GMAIL_ACCOUNT_1_CATEGORY=executive
GMAIL_ACCOUNT_1_REFRESH_TOKEN=...

GMAIL_ACCOUNT_2_EMAIL=transactions@enchantaz.com
GMAIL_ACCOUNT_2_CATEGORY=transactions
GMAIL_ACCOUNT_2_REFRESH_TOKEN=...

GMAIL_ACCOUNT_3_EMAIL=adam@adamprather.com
GMAIL_ACCOUNT_3_CATEGORY=executive
GMAIL_ACCOUNT_3_REFRESH_TOKEN=...

GMAIL_ACCOUNT_4_EMAIL=adam.prather@gmail.com
GMAIL_ACCOUNT_4_CATEGORY=executive
GMAIL_ACCOUNT_4_REFRESH_TOKEN=...
```

(Category assignments match what's already tagged in the existing n8n
workflow — adjust if you want different groupings.)

## Step 6 — Test

- `curl https://your-domain/health` should list all 4 account emails.
- `curl -X POST https://your-domain/run-now` triggers an immediate check of
  all 4 inboxes, without waiting for the hourly schedule — use this to
  verify it's working end-to-end before trusting the schedule.
- Check the `emails` and `tasks` tables in Supabase for new rows after
  running `/run-now`.

## What this does NOT do yet

- Doesn't handle Gmail's `historyId`-based incremental sync (uses simple
  timestamp tracking instead) — fine at this volume, worth upgrading later
  if you want zero chance of ever double-processing a message during a
  crash/restart.
- Doesn't replicate the Email Reply Assistant — that's a separate, later piece.
- n8n's 4-inbox workflow stays running until you've confirmed this one
  works reliably — don't turn it off yet.
