"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";

import { createSubject, deleteSubject } from "@/lib/queries/subjects";
import { createClient } from "@/lib/supabase/server";
import { createSubjectSchema } from "@/lib/validation/subject";

export type FormState = {
  /** Field name -> message. Shown next to the offending input. */
  fieldErrors?: Record<string, string>;
  /** Something went wrong that isn't the student's fault. */
  formError?: string;
};

/**
 * Validates on the server and writes the subject.
 *
 * The browser does its own checks too, but those are a convenience, not a
 * guarantee — anyone can post whatever they like to this action. This is the
 * check that counts.
 */
export async function createSubjectAction(
  _prevState: FormState,
  formData: FormData,
): Promise<FormState> {
  const parsed = createSubjectSchema.safeParse({
    name: formData.get("name"),
    level: formData.get("level"),
    examDate: formData.get("examDate"),
  });

  if (!parsed.success) {
    const fieldErrors: Record<string, string> = {};
    for (const issue of parsed.error.issues) {
      const field = String(issue.path[0] ?? "form");
      // Keep the first message per field — a stack of three messages under one
      // input is noise, not help.
      fieldErrors[field] ??= issue.message;
    }
    return { fieldErrors };
  }

  try {
    await createSubject(parsed.data);
  } catch (error) {
    console.error("createSubjectAction failed", error);
    return {
      formError:
        "We couldn't save that. Your connection may have dropped — try again.",
    };
  }

  revalidatePath("/dashboard");
  return {};
}

export async function deleteSubjectAction(formData: FormData): Promise<void> {
  const id = String(formData.get("id") ?? "");
  if (!id) return;

  try {
    await deleteSubject(id);
  } catch (error) {
    console.error("deleteSubjectAction failed", error);
    // Deletion failing is rare and non-destructive — the subject is simply
    // still there. Re-rendering shows the student the true state.
  }

  revalidatePath("/dashboard");
}

export async function signOutAction(): Promise<void> {
  const supabase = await createClient();
  await supabase.auth.signOut();
  redirect("/login");
}
