-- CRAM it — initial schema
--
-- Run this in the Supabase dashboard: SQL Editor -> New query -> paste -> Run.
--
-- Two tables:
--   profiles  - one row per user, created automatically on signup
--   subjects  - a thing the student is revising for, with an exam date
--
-- Every table has Row Level Security (RLS) turned on. RLS is the wall between
-- one student's data and another's. Without it, anyone with the public API key
-- could read every row in the database. It is not optional.

-- ---------------------------------------------------------------------------
-- profiles
-- ---------------------------------------------------------------------------

create table if not exists public.profiles (
  -- Same id as the user in Supabase's built-in auth.users table.
  -- "on delete cascade" means: if the user is deleted, this row goes too.
  id uuid primary key references auth.users (id) on delete cascade,
  created_at timestamptz not null default now()
);

alter table public.profiles enable row level security;

-- In plain English: "you may read your own profile row, and nobody else's."
-- auth.uid() is the id of whoever is making the request right now.
create policy "profiles: read own"
  on public.profiles for select
  using (auth.uid() = id);

-- In plain English: "you may update your own profile row, and nobody else's."
create policy "profiles: update own"
  on public.profiles for update
  using (auth.uid() = id)
  with check (auth.uid() = id);

-- ---------------------------------------------------------------------------
-- Create a profile automatically whenever someone signs up
-- ---------------------------------------------------------------------------
-- Without this, a new user exists in auth.users but has no profile row, and
-- anything that joins against profiles quietly returns nothing.

create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  insert into public.profiles (id) values (new.id)
  on conflict (id) do nothing;
  return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;

create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();

-- ---------------------------------------------------------------------------
-- subjects
-- ---------------------------------------------------------------------------

create table if not exists public.subjects (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,

  name text not null check (char_length(trim(name)) between 1 and 80),

  -- A calendar date, not a timestamp. "My exam is on the 14th" has no time
  -- attached to it, and storing a timestamp would drag timezones into a
  -- countdown that should just say "11 days".
  exam_date date not null,

  -- e.g. 'GCSE', 'A Level', 'Undergraduate'. Free text on purpose: guessing
  -- the world's exam systems into an enum is a fight we do not need. The card
  -- generator sends this to Claude so it pitches the difficulty correctly.
  level text not null check (char_length(trim(level)) between 1 and 40),

  created_at timestamptz not null default now()
);

-- Makes "show me my subjects, soonest exam first" fast once a user has many.
create index if not exists subjects_user_id_exam_date_idx
  on public.subjects (user_id, exam_date);

alter table public.subjects enable row level security;

-- The four policies below are the whole security model for this table.
-- Read them as: "a row is yours if its user_id is you."

-- "You may read subjects you own."
create policy "subjects: read own"
  on public.subjects for select
  using (auth.uid() = user_id);

-- "You may create a subject, but only one stamped with your own user_id."
-- The `with check` half is what stops someone crafting a request that inserts
-- a row belonging to another user.
create policy "subjects: insert own"
  on public.subjects for insert
  with check (auth.uid() = user_id);

-- "You may edit subjects you own, and you may not hand them to someone else."
create policy "subjects: update own"
  on public.subjects for update
  using (auth.uid() = user_id)
  with check (auth.uid() = user_id);

-- "You may delete subjects you own."
create policy "subjects: delete own"
  on public.subjects for delete
  using (auth.uid() = user_id);
