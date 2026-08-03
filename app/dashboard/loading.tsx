/**
 * Shown while the dashboard's data loads. The shapes match the real cards, so
 * nothing jumps when the content arrives.
 *
 * Next.js only shows this if loading actually takes a moment — a fast load
 * skips it entirely, which is why there's no artificial delay here.
 */
export default function DashboardLoading() {
  return (
    <div className="flex min-h-dvh flex-col">
      <div aria-hidden className="h-[3px] w-full bg-line" />

      <main className="mx-auto w-full max-w-2xl flex-1 px-5 py-10">
        <div className="flex items-baseline justify-between">
          <h1 className="font-[family-name:var(--font-display)] text-3xl uppercase tracking-tight">
            CRAM it
          </h1>
        </div>

        <div
          role="status"
          aria-label="Loading your subjects"
          className="mt-10 space-y-4"
        >
          {[0, 1].map((index) => (
            <div
              key={index}
              className="flex items-center gap-5 rounded-sm border border-line bg-paper p-5"
            >
              <div className="min-w-0 flex-1">
                <div className="h-6 w-40 animate-pulse rounded-sm bg-line" />
                <div className="mt-2 h-4 w-56 animate-pulse rounded-sm bg-line" />
              </div>
              <div className="shrink-0 text-right">
                <div className="h-12 w-16 animate-pulse rounded-sm bg-line" />
                <div className="mt-1 h-3 w-14 animate-pulse rounded-sm bg-line" />
              </div>
            </div>
          ))}
        </div>
      </main>
    </div>
  );
}
