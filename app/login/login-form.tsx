"use client";

import { useState } from "react";

import { createClient } from "@/lib/supabase/client";

type Status =
  | { kind: "idle" }
  | { kind: "sending" }
  | { kind: "sent"; email: string }
  | { kind: "error"; message: string };

export function LoginForm({ initialError }: { initialError?: string }) {
  const [email, setEmail] = useState("");
  const [status, setStatus] = useState<Status>(
    initialError ? { kind: "error", message: initialError } : { kind: "idle" },
  );

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();

    const trimmed = email.trim();
    if (!trimmed.includes("@")) {
      setStatus({
        kind: "error",
        message: "That doesn't look like an email address.",
      });
      return;
    }

    setStatus({ kind: "sending" });

    const supabase = createClient();
    const { error } = await supabase.auth.signInWithOtp({
      email: trimmed,
      options: { emailRedirectTo: `${window.location.origin}/auth/callback` },
    });

    if (error) {
      setStatus({
        kind: "error",
        message:
          "We couldn't send that link. Check the address and try again in a moment.",
      });
      return;
    }

    setStatus({ kind: "sent", email: trimmed });
  }

  if (status.kind === "sent") {
    return (
      <>
        <h1 className="font-[family-name:var(--font-display)] text-5xl uppercase leading-[0.95] tracking-tight">
          Check your email
        </h1>
        <p className="mt-4 text-muted">
          We sent a sign-in link to{" "}
          <span className="text-text">{status.email}</span>. Open it on this
          device and you&apos;re in.
        </p>
        <button
          type="button"
          onClick={() => {
            setStatus({ kind: "idle" });
            setEmail("");
          }}
          className="mt-8 inline-flex min-h-[44px] items-center self-start text-muted underline underline-offset-4 hover:text-text"
        >
          Use a different address
        </button>
      </>
    );
  }

  const sending = status.kind === "sending";

  return (
    <>
      <h1 className="font-[family-name:var(--font-display)] text-6xl uppercase leading-[0.95] tracking-tight">
        CRAM it
      </h1>
      <p className="mt-3 text-muted">
        Tells you what to study right now, then makes you recall it.
      </p>

      <form onSubmit={handleSubmit} className="mt-10" noValidate>
        <label htmlFor="email" className="block text-sm text-muted">
          Email
        </label>
        <input
          id="email"
          name="email"
          type="email"
          inputMode="email"
          autoComplete="email"
          autoFocus
          required
          value={email}
          onChange={(event) => {
            setEmail(event.target.value);
            if (status.kind === "error") setStatus({ kind: "idle" });
          }}
          aria-invalid={status.kind === "error"}
          aria-describedby={status.kind === "error" ? "email-error" : undefined}
          className="mt-2 min-h-[52px] w-full rounded-sm border border-line bg-paper px-4 text-text placeholder:text-muted focus:border-text focus:outline-none"
          placeholder="you@school.uk"
        />

        {status.kind === "error" && (
          <p id="email-error" role="alert" className="mt-3 text-sm text-mark">
            {status.message}
          </p>
        )}

        <button
          type="submit"
          disabled={sending}
          className="mt-6 min-h-[52px] w-full rounded-sm bg-mark px-5 font-medium text-ink transition-opacity hover:opacity-90 disabled:opacity-60"
        >
          {sending ? "Sending link…" : "Send me a sign-in link"}
        </button>
      </form>

      <p className="mt-6 text-sm text-muted">
        No password. We email you a link that signs you in.
      </p>
    </>
  );
}
