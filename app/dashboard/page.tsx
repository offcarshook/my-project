import {
  countdownLabel,
  countdownValue,
  daysUntil,
  formatExamDate,
  isUrgent,
} from "@/lib/dates";
import { listSubjects, type Subject } from "@/lib/queries/subjects";

import { deleteSubjectAction, signOutAction } from "./actions";
import { AddSubjectForm } from "./add-subject-form";

// Always render fresh: a countdown that says "3 days left" when it's 2 is
// worse than a slightly slower page.
export const dynamic = "force-dynamic";

export default async function DashboardPage() {
  let subjects: Subject[] = [];
  let loadError: string | undefined;

  try {
    subjects = await listSubjects();
  } catch (error) {
    console.error("dashboard load failed", error);
    loadError =
      "We couldn't reach your subjects just now. Refresh the page to try again.";
  }

  const soonest = subjects[0];

  return (
    <div className="flex min-h-dvh flex-col">
      {/* The time bar. In Sprint 3 it drains through a study session; here it
          simply marks how close the nearest exam is. Built once, reused. */}
      <TimeBar days={soonest ? daysUntil(soonest.exam_date) : undefined} />

      <main className="mx-auto w-full max-w-2xl flex-1 px-5 py-10">
        <header className="flex items-baseline justify-between">
          <h1 className="font-[family-name:var(--font-display)] text-3xl uppercase tracking-tight">
            CRAM it
          </h1>
          <form action={signOutAction}>
            <button
              type="submit"
              className="min-h-[44px] text-sm text-muted underline underline-offset-4 hover:text-text"
            >
              Sign out
            </button>
          </form>
        </header>

        {loadError ? (
          <p role="alert" className="mt-10 text-mark">
            {loadError}
          </p>
        ) : subjects.length === 0 ? (
          <EmptyState />
        ) : (
          <ul className="mt-10 space-y-4">
            {subjects.map((subject) => (
              <li key={subject.id}>
                <SubjectCard subject={subject} />
              </li>
            ))}
          </ul>
        )}

        {!loadError && (
          <div className="mt-8">
            <AddSubjectForm autoOpen={subjects.length === 0} />
          </div>
        )}
      </main>
    </div>
  );
}

function EmptyState() {
  return (
    <div className="mt-10">
      <h2 className="font-[family-name:var(--font-display)] text-4xl uppercase leading-[0.95] tracking-tight">
        What are you sitting?
      </h2>
      <p className="mt-3 text-muted">
        Add your first subject and its exam date. You&apos;ll see exactly how
        long you have.
      </p>
    </div>
  );
}

function SubjectCard({ subject }: { subject: Subject }) {
  const days = daysUntil(subject.exam_date);
  const urgent = isUrgent(days);
  const past = days < 0;

  return (
    <article className="flex items-center gap-5 rounded-sm border border-line bg-paper p-5">
      <div className="min-w-0 flex-1">
        <h2 className="truncate text-xl text-text">{subject.name}</h2>
        <p className="mt-1 text-sm text-muted">
          {subject.level} · {formatExamDate(subject.exam_date)}
        </p>
      </div>

      <div className="shrink-0 text-right">
        <div
          className={`tabular text-5xl leading-none ${
            urgent ? "text-mark" : past ? "text-muted" : "text-text"
          }`}
        >
          {countdownValue(days)}
        </div>
        <div className="mt-1 text-xs uppercase tracking-wide text-muted">
          {countdownLabel(days)}
        </div>
      </div>

      <form action={deleteSubjectAction} className="shrink-0">
        <input type="hidden" name="id" value={subject.id} />
        <button
          type="submit"
          aria-label={`Remove ${subject.name}`}
          className="flex h-[44px] w-[44px] items-center justify-center rounded-sm text-muted hover:text-text"
        >
          ×
        </button>
      </form>
    </article>
  );
}

/**
 * The exam-hall clock: a thin bar across the top of every screen. It fills as
 * the exam approaches and turns red inside the final stretch.
 */
function TimeBar({ days }: { days?: number }) {
  if (days === undefined) {
    return <div aria-hidden className="h-[3px] w-full bg-line" />;
  }

  // 60 days out reads as empty; exam day reads as full.
  const elapsed = Math.min(Math.max((60 - days) / 60, 0), 1);
  const urgent = isUrgent(days);

  return (
    <div aria-hidden className="h-[3px] w-full bg-line">
      <div
        className={`h-full transition-[width] duration-300 ${
          urgent ? "bg-mark" : "bg-muted"
        }`}
        style={{ width: `${elapsed * 100}%` }}
      />
    </div>
  );
}
