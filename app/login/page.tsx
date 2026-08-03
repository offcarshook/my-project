import { LoginForm } from "./login-form";

/**
 * Turns the ?error= code set by /auth/callback into something a person can
 * act on. Never show the raw code.
 */
function errorMessage(code: string | undefined): string | undefined {
  switch (code) {
    case "link_expired":
      return "That link has expired. Enter your email and we'll send a fresh one.";
    case "link_invalid":
      return "That link didn't work. Enter your email and we'll send a new one.";
    default:
      return undefined;
  }
}

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ error?: string }>;
}) {
  const { error } = await searchParams;

  return (
    <main className="mx-auto flex w-full max-w-md flex-1 flex-col justify-center px-5 py-12">
      <LoginForm initialError={errorMessage(error)} />
    </main>
  );
}
