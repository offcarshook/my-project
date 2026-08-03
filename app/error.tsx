"use client";

import { useEffect } from "react";

/**
 * The last line of defence. If anything throws that wasn't caught closer to
 * where it happened, this is what the student sees — never a stack trace.
 */
export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <main className="mx-auto flex w-full max-w-md flex-1 flex-col justify-center px-5 py-12">
      <h1 className="font-[family-name:var(--font-display)] text-4xl uppercase tracking-tight">
        That didn&apos;t load
      </h1>
      <p className="mt-4 text-muted">
        Something broke on our side, not yours. Try again — if it keeps
        happening, close the tab and come back in a minute.
      </p>
      <button
        type="button"
        onClick={reset}
        className="mt-8 min-h-[52px] rounded-sm bg-text px-5 font-medium text-ink transition-opacity hover:opacity-90"
      >
        Try again
      </button>
    </main>
  );
}
