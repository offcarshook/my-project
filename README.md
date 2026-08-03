# CRAM it

A student picks a subject, sets the exam date, uploads their material, and the
app tells them exactly what to study right now — then makes them recall it.

**Success metric:** a stressed student with an exam in 3 days opens the app and
is actively recalling information within 60 seconds. Not reading. Not
organising. Recalling.

---

## Where the project is

**Sprint 1 — the spine. Done.** Sign in, add a subject with an exam date, see
the countdown.

Not built yet: flashcards, AI, cram sessions. Those are Sprints 2–6.

---

## Running it locally

You need Node 20 or newer.

```bash
npm install
npm run dev
```

Then open <http://localhost:3000>.

This needs `.env.local` in the project root with two values from Supabase
(Project Settings → API Keys):

```
NEXT_PUBLIC_SUPABASE_URL=https://xxxxx.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=sb_publishable_xxxxx
```

Both are safe to expose in the browser — that's what `NEXT_PUBLIC_` means, and
it's intentional. Row Level Security is what protects the data, not key
secrecy. **Never put the `service_role` key in this file.**

`.env.local` is gitignored and must stay that way.

---

## Setting up the database

Run `supabase/migrations/0001_init.sql` once, in the Supabase dashboard:

1. SQL Editor → New query
2. Paste the whole file
3. Run

It creates the `profiles` and `subjects` tables and the Row Level Security
policies that keep one student's subjects invisible to every other student.

### Prove RLS actually works

Do this before building anything on top of it. It takes two minutes and it is
the difference between a working app and a data breach.

1. Sign in as yourself, add a subject called `Account A test`.
2. Open a private/incognito window, sign in with a **different** email address.
3. That second account must show an empty dashboard — no `Account A test`.

If account B can see account A's subject, stop and fix the policies before
writing another line.

---

## How the code is arranged

```
app/
  page.tsx              front door — redirects to login or dashboard
  login/                magic-link sign-in
  auth/callback/        where the emailed link lands
  dashboard/            subject list, countdown, add form
lib/
  dates.ts              countdown maths (timezone-safe)
  queries/subjects.ts   every database read and write
  supabase/             client setup for browser and server
  validation/           Zod schemas
proxy.ts                refreshes the session, guards private routes
supabase/migrations/    the schema and RLS policies
```

Rules this codebase follows:

- **Every database call goes through `lib/queries/`.** No Supabase calls
  scattered through components.
- **Server Components fetch data.** `"use client"` only where there's real
  interactivity.
- **Never trust the client.** The signed-in user's id is read from the session
  on the server, never from a form field.
- **RLS on every table, from day one.**

---

## Deploying

Push the branch, then in Vercel: import the repo, add the same two environment
variables, deploy.

After the first deploy, add your Vercel URL to Supabase under **Authentication
→ URL Configuration → Redirect URLs** as `https://your-app.vercel.app/**`, or
the emailed sign-in links will bounce users back to localhost.
