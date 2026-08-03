import { cookies } from "next/headers";
import { createServerClient } from "@supabase/ssr";

import { supabaseAnonKey, supabaseUrl } from "./env";

/**
 * Supabase client for use in Server Components, Server Actions, and Route
 * Handlers.
 *
 * The session lives in cookies. This client reads them so the database knows
 * who is asking — which is what makes the RLS policies in
 * supabase/migrations/0001_init.sql do their job.
 */
export async function createClient() {
  const cookieStore = await cookies();

  return createServerClient(supabaseUrl, supabaseAnonKey, {
    cookies: {
      getAll() {
        return cookieStore.getAll();
      },
      setAll(cookiesToSet) {
        try {
          for (const { name, value, options } of cookiesToSet) {
            cookieStore.set(name, value, options);
          }
        } catch {
          // Server Components are not allowed to set cookies. That's fine —
          // middleware.ts refreshes the session on every request, so the
          // cookies are always current by the time a page renders.
        }
      },
    },
  });
}
