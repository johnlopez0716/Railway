# Enchant Webhooks — Fireflies receiver

Part of the Enchant Executive Assistant dashboard project. This is Stage 3
of migrating the dashboard's automation off n8n, piece by piece (see
n8n-to-railway-migration-plan.md) — a thin FastAPI service that receives a
Fireflies meeting webhook, pulls the full transcript/summary from Fireflies'
API, runs the same AI-importance triage already used for email and calendar,
and writes to the same Supabase project the dashboard already reads from.

This does not replace the dashboard or change anything about it — it adds
one new data source (`meetings` table) using the exact same account_email /
category tagging convention as everything else.

## 1. Run the Supabase migration first

In the Supabase SQL editor (the same Enchant Supabase project the dashboard
uses), run `supabase_migration.sql` from this folder. This adds the
`meetings` table.

## 2. Deploy to Railway (run locally, not in this chat)

```bash
cd enchant-webhooks
railway link
railway up
```

## 3. Set environment variables

```bash
railway variables set SUPABASE_URL=https://your-project.supabase.co
railway variables set SUPABASE_SERVICE_KEY=your-service-role-key
railway variables set FIREFLIES_API_KEY=your-fireflies-api-key
railway variables set FIREFLIES_WEBHOOK_SECRET=your-fireflies-webhook-secret
railway variables set ANTHROPIC_API_KEY=your-anthropic-api-key
railway variables set MEETINGS_ACCOUNT_EMAIL=adam@adamprather.com
railway variables set MEETINGS_CATEGORY=executive
```

Use the Supabase **service role key** (Settings → API), not the anon key.

## 4. Generate a public domain

```bash
railway domain
```

## 5. Point Fireflies at it

Fireflies → Settings → Integrations → Webhooks:
- Webhook URL: `https://your-railway-domain/webhooks/fireflies`
- Copy the webhook secret Fireflies generates into `FIREFLIES_WEBHOOK_SECRET` above
- Your Fireflies API key is `FIREFLIES_API_KEY` above

## 6. Test

- `curl https://your-railway-domain/health` should return `{"status":"ok"}`
- Send me the URL once live and I'll verify it from here
- Once confirmed working, next dashboard step is a "Meetings" section on the
  Executive Assistant page reading from this new table — same pattern as the
  email/calendar sections already built
