import { NextResponse, type NextRequest } from "next/server";

import { createClient } from "@/lib/supabase/server";

/**
 * Where the magic link in the student's email lands.
 *
 * Supabase sends them here with a one-time `code`. We trade that code for a
 * session cookie and send them on to the dashboard. If anything is wrong with
 * the code — expired, already used, tampered with — we send them back to the
 * login page with a message, rather than showing a stack trace.
 */
export async function GET(request: NextRequest) {
  const { searchParams, origin } = request.nextUrl;
  const code = searchParams.get("code");

  if (!code) {
    return NextResponse.redirect(`${origin}/login?error=link_invalid`);
  }

  const supabase = await createClient();
  const { error } = await supabase.auth.exchangeCodeForSession(code);

  if (error) {
    // Almost always an expired or already-used link.
    return NextResponse.redirect(`${origin}/login?error=link_expired`);
  }

  return NextResponse.redirect(`${origin}/dashboard`);
}
