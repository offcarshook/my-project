import { createBrowserClient } from "@supabase/ssr";

import { supabaseAnonKey, supabaseUrl } from "./env";

/**
 * Supabase client for use in Client Components ("use client").
 *
 * Only ever sees the publishable key, which is safe in the browser — RLS is
 * what actually protects the data, not key secrecy.
 */
export function createClient() {
  return createBrowserClient(supabaseUrl, supabaseAnonKey);
}
