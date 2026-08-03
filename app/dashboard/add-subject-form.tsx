"use client";

import { useActionState, useEffect, useRef, useState } from "react";

import { todayIsoDate } from "@/lib/dates";
import { subjectLevels } from "@/lib/validation/subject";

import { createSubjectAction, type FormState } from "./actions";

/**
 * The add-subject form, collapsed to a single button until it's needed.
 *
 * Collapsed by default because the dashboard's job is the countdown, not data
 * entry — but it opens automatically when the student has no subjects yet,
 * because an empty screen should be an invitation, not a void.
 */
export function AddSubjectForm({ autoOpen = false }: { autoOpen?: boolean }) {
  const [open, setOpen] = useState(autoOpen);
  const [state, formAction, pending] = useActionState<FormState, FormData>(
    createSubjectAction,
    {},
  );
  const formRef = useRef<HTMLFormElement>(null);
  const nameRef = useRef<HTMLInputElement>(null);

  // Clear the form after a successful save so a second subject is quick to add.
  useEffect(() => {
    if (!pending && state && !state.fieldErrors && !state.formError) {
      formRef.current?.reset();
    }
  }, [state, pending]);

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => {
          setOpen(true);
          // Focus lands on the first field, so keyboard users aren't stranded.
          requestAnimationFrame(() => nameRef.current?.focus());
        }}
        className="min-h-[52px] w-full rounded-sm border border-line bg-paper px-5 text-left text-text hover:border-muted"
      >
        + Add a subject
      </button>
    );
  }

  return (
    <form
      ref={formRef}
      action={formAction}
      noValidate
      className="rounded-sm border border-line bg-paper p-5"
    >
      <div>
        <label htmlFor="name" className="block text-sm text-muted">
          Subject
        </label>
        <input
          ref={nameRef}
          id="name"
          name="name"
          type="text"
          required
          maxLength={80}
          autoComplete="off"
          aria-invalid={Boolean(state.fieldErrors?.name)}
          aria-describedby={state.fieldErrors?.name ? "name-error" : undefined}
          placeholder="Biology"
          className="mt-2 min-h-[52px] w-full rounded-sm border border-line bg-ink px-4 text-text placeholder:text-muted focus:border-text focus:outline-none"
        />
        {state.fieldErrors?.name && (
          <p id="name-error" role="alert" className="mt-2 text-sm text-mark">
            {state.fieldErrors.name}
          </p>
        )}
      </div>

      <div className="mt-5">
        <label htmlFor="level" className="block text-sm text-muted">
          Level
        </label>
        <select
          id="level"
          name="level"
          required
          defaultValue=""
          aria-invalid={Boolean(state.fieldErrors?.level)}
          aria-describedby={state.fieldErrors?.level ? "level-error" : undefined}
          className="mt-2 min-h-[52px] w-full rounded-sm border border-line bg-ink px-4 text-text focus:border-text focus:outline-none"
        >
          <option value="" disabled>
            Choose one
          </option>
          {subjectLevels.map((level) => (
            <option key={level} value={level}>
              {level}
            </option>
          ))}
        </select>
        {state.fieldErrors?.level && (
          <p id="level-error" role="alert" className="mt-2 text-sm text-mark">
            {state.fieldErrors.level}
          </p>
        )}
      </div>

      <div className="mt-5">
        <label htmlFor="examDate" className="block text-sm text-muted">
          Exam date
        </label>
        <input
          id="examDate"
          name="examDate"
          type="date"
          required
          min={todayIsoDate()}
          aria-invalid={Boolean(state.fieldErrors?.examDate)}
          aria-describedby={
            state.fieldErrors?.examDate ? "examDate-error" : undefined
          }
          className="mt-2 min-h-[52px] w-full rounded-sm border border-line bg-ink px-4 text-text focus:border-text focus:outline-none"
        />
        {state.fieldErrors?.examDate && (
          <p id="examDate-error" role="alert" className="mt-2 text-sm text-mark">
            {state.fieldErrors.examDate}
          </p>
        )}
      </div>

      {state.formError && (
        <p role="alert" className="mt-5 text-sm text-mark">
          {state.formError}
        </p>
      )}

      <div className="mt-6 flex gap-3">
        <button
          type="submit"
          disabled={pending}
          className="min-h-[52px] flex-1 rounded-sm bg-text px-5 font-medium text-ink transition-opacity hover:opacity-90 disabled:opacity-60"
        >
          {pending ? "Saving…" : "Save subject"}
        </button>
        <button
          type="button"
          onClick={() => setOpen(false)}
          className="min-h-[52px] rounded-sm border border-line px-5 text-muted hover:text-text"
        >
          Cancel
        </button>
      </div>
    </form>
  );
}
