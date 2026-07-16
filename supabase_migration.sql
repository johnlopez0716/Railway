create table public.meetings (
  id bigint generated always as identity primary key,
  created_at timestamptz default now(),
  fireflies_meeting_id text unique not null,
  title text,
  summary text,
  action_items text,
  attendees text,
  duration_minutes numeric,
  account_email text,
  category text,
  is_important boolean default false,
  occurred_at timestamptz
);

alter table public.meetings enable row level security;

create policy "Allow all access to meetings"
  on public.meetings
  for all
  using (true)
  with check (true);

NOTIFY pgrst, 'reload schema';
