/**
 * Reads the two Supabase environment variables, and fails loudly if either is
 * missing.
 *
 * Without this, a missing variable shows up much later as a confusing "invalid
 * URL" or "failed to fetch" error, usually in production. Better to break at
 * startup with a message that says exactly what to do.
 *
 * Both variables are NEXT_PUBLIC_ on purpose: they are designed to be visible
 * in the browser. Never put the service_role key in a NEXT_PUBLIC_ variable —
 * that key bypasses RLS entirely.
 */

function required(name: string, value: string | undefined): string {
  if (!value) {
    throw new Error(
      `Missing environment variable ${name}. ` +
        `Add it to .env.local for local development, and to the Environment ` +
        `Variables section of your Vercel project for deployment. ` +
        `You'll find the value in Supabase under Project Settings -> API Keys.`,
    );
  }
  return value;
}

export const supabaseUrl = required(
  "NEXT_PUBLIC_SUPABASE_URL",
  process.env.NEXT_PUBLIC_SUPABASE_URL,
);

export const supabaseAnonKey = required(
  "NEXT_PUBLIC_SUPABASE_ANON_KEY",
  process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY,
);
