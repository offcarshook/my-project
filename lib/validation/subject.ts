import { z } from "zod";

import { daysUntil } from "@/lib/dates";

/**
 * Validation for the "add a subject" form.
 *
 * Every message here is written to be read by a stressed seventeen-year-old at
 * 11pm. No field names, no jargon, no "invalid input".
 */

export const subjectLevels = [
  "GCSE",
  "A Level",
  "IB",
  "BTEC",
  "Undergraduate",
  "Postgraduate",
  "Other",
] as const;

export const createSubjectSchema = z.object({
  name: z
    .string()
    .trim()
    .min(1, "Give the subject a name.")
    .max(80, "That name is too long — 80 characters at most."),

  level: z
    .string()
    .trim()
    .min(1, "Pick what level you're studying at.")
    .max(40, "That level name is too long.")
    .refine(
      (value) => (subjectLevels as readonly string[]).includes(value),
      "Pick one of the levels from the list.",
    ),

  examDate: z
    .string()
    .trim()
    .regex(/^\d{4}-\d{2}-\d{2}$/, "Pick the date of your exam.")
    .refine((value) => {
      const [year, month, day] = value.split("-").map(Number);
      const date = new Date(Date.UTC(year, month - 1, day, 12));
      // Catches things like 2026-02-31, which pass the regex but aren't real.
      return (
        date.getUTCFullYear() === year &&
        date.getUTCMonth() === month - 1 &&
        date.getUTCDate() === day
      );
    }, "That date doesn't exist. Check the day and month.")
    .refine(
      (value) => daysUntil(value) >= 0,
      "That date has already passed. Pick your next exam.",
    )
    .refine(
      (value) => daysUntil(value) <= 365 * 3,
      "That's more than three years away. Check the year.",
    ),
});

export type CreateSubjectInput = z.infer<typeof createSubjectSchema>;
