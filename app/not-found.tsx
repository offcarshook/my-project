import Link from "next/link";

export default function NotFound() {
  return (
    <main className="mx-auto flex w-full max-w-md flex-1 flex-col justify-center px-5 py-12">
      <h1 className="font-[family-name:var(--font-display)] text-4xl uppercase tracking-tight">
        Nothing here
      </h1>
      <p className="mt-4 text-muted">
        That page doesn&apos;t exist. Your subjects are where you left them.
      </p>
      <Link
        href="/dashboard"
        className="mt-8 inline-flex min-h-[52px] items-center justify-center rounded-sm bg-text px-5 font-medium text-ink transition-opacity hover:opacity-90"
      >
        Back to my subjects
      </Link>
    </main>
  );
}
