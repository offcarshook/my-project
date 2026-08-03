import { redirect } from "next/navigation";

import { createClient } from "@/lib/supabase/server";

/**
 * The front door. Signed in goes to the dashboard, signed out goes to login.
 *
 * There is deliberately no marketing page. A student arriving here is already
 * frightened and has already decided — a landing page would be one more screen
 * between them and remembering something.
 */
export default async function HomePage() {
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();

  redirect(user ? "/dashboard" : "/login");
}
