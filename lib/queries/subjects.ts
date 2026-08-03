import { createClient } from "@/lib/supabase/server";
import type { CreateSubjectInput } from "@/lib/validation/subject";

/**
 * Every read and write of the `subjects` table happens here. Nothing else in
 * the app talks to Supabase directly — when a query is wrong, this is the only
 * file you have to open.
 *
 * Note what's missing from these functions: any check that the rows belong to
 * the current user. That check lives in the database, in the RLS policies in
 * supabase/migrations/0001_init.sql. Enforcing it there rather than here means
 * a forgotten `.eq("user_id", ...)` cannot leak another student's data.
 */

export type Subject = {
  id: string;
  user_id: string;
  name: string;
  exam_date: string; // "YYYY-MM-DD"
  level: string;
  created_at: string;
};

/** The student's subjects, soonest exam first. */
export async function listSubjects(): Promise<Subject[]> {
  const supabase = await createClient();

  const { data, error } = await supabase
    .from("subjects")
    .select("id, user_id, name, exam_date, level, created_at")
    .order("exam_date", { ascending: true });

  if (error) {
    throw new Error(`Could not load subjects: ${error.message}`);
  }

  return data ?? [];
}

/**
 * Creates a subject owned by the signed-in user.
 *
 * The user_id is read from the session on the server, never from the form.
 * Trusting the client with it would let anyone create rows under someone
 * else's account — though the RLS insert policy would reject that too. Two
 * locks on the same door, on purpose.
 */
export async function createSubject(input: CreateSubjectInput): Promise<Subject> {
  const supabase = await createClient();

  const {
    data: { user },
  } = await supabase.auth.getUser();

  if (!user) {
    throw new Error("You've been signed out. Sign in again to add a subject.");
  }

  const { data, error } = await supabase
    .from("subjects")
    .insert({
      user_id: user.id,
      name: input.name,
      level: input.level,
      exam_date: input.examDate,
    })
    .select("id, user_id, name, exam_date, level, created_at")
    .single();

  if (error) {
    throw new Error(`Could not save that subject: ${error.message}`);
  }

  return data;
}

/** Removes one of the student's own subjects. */
export async function deleteSubject(id: string): Promise<void> {
  const supabase = await createClient();

  const { error } = await supabase.from("subjects").delete().eq("id", id);

  if (error) {
    throw new Error(`Could not delete that subject: ${error.message}`);
  }
}
