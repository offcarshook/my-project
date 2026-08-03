/**
 * Date helpers for the exam countdown.
 *
 * Exam dates are plain calendar dates ("2026-06-14"), not timestamps. A
 * student thinks "my exam is on the 14th", not "my exam is at midnight UTC on
 * the 14th". Mixing the two is how countdowns end up one day out for anyone
 * not sitting in London, so every calculation here anchors to midday UTC,
 * which is far enough from a date boundary that no timezone can push it over.
 */

const MS_PER_DAY = 24 * 60 * 60 * 1000;

/** Turns "2026-06-14" into a Date at midday UTC on that day. */
function toUtcNoon(isoDate: string): Date {
  const [year, month, day] = isoDate.split("-").map(Number);
  return new Date(Date.UTC(year, month - 1, day, 12, 0, 0));
}

/** Today, as a Date at midday UTC. */
function todayUtcNoon(now: Date = new Date()): Date {
  return new Date(
    Date.UTC(now.getFullYear(), now.getMonth(), now.getDate(), 12, 0, 0),
  );
}

/** Today as "YYYY-MM-DD", for min= on a date input. */
export function todayIsoDate(now: Date = new Date()): string {
  const year = now.getFullYear();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

/**
 * Whole days from today until the exam.
 *
 *   2  -> exam is the day after tomorrow
 *   0  -> exam is today
 *  -1  -> exam was yesterday
 */
export function daysUntil(isoDate: string, now: Date = new Date()): number {
  const diff = toUtcNoon(isoDate).getTime() - todayUtcNoon(now).getTime();
  return Math.round(diff / MS_PER_DAY);
}

/**
 * How the countdown reads on screen. Short on purpose — this sits under a very
 * large number and should not compete with it.
 */
export function countdownLabel(days: number): string {
  if (days < 0) return days === -1 ? "day ago" : "days ago";
  if (days === 0) return "today";
  return days === 1 ? "day left" : "days left";
}

/** The big number itself. Past exams show how long ago, not a minus sign. */
export function countdownValue(days: number): string {
  if (days === 0) return "NOW";
  return String(Math.abs(days));
}

/**
 * True when the exam is close enough that the countdown turns red.
 * Two days is the point where "there isn't time for everything" becomes true.
 */
export function isUrgent(days: number): boolean {
  return days >= 0 && days <= 2;
}

/** e.g. "Sat 14 Jun 2026" */
export function formatExamDate(isoDate: string): string {
  return toUtcNoon(isoDate).toLocaleDateString("en-GB", {
    weekday: "short",
    day: "numeric",
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  });
}
