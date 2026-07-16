create table public.sync_state (
  account_email text primary key,
  last_synced_at timestamptz
);

alter table public.sync_state enable row level security;

create policy "Allow all access to sync_state"
  on public.sync_state
  for all
  using (true)
  with check (true);

NOTIFY pgrst, 'reload schema';
