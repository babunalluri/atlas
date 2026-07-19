import { SignUp } from "@clerk/nextjs";
import Link from "next/link";

export default function SignUpPage() {
  const publishableKey = process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY;
  const clerkReady =
    !!publishableKey && !publishableKey.includes("replace_me");

  if (!clerkReady) {
    return (
      <main className="flex min-h-screen items-center justify-center px-4">
        <section className="surface-panel max-w-md rounded-2xl p-8 text-center">
          <h1 className="font-display text-3xl font-semibold">
            Clerk is not configured
          </h1>
          <p className="mt-3 text-sm text-slate-muted">
            Account creation is disabled in local development mode.
          </p>
          <Link
            href="/admin/agents"
            className="mt-6 inline-flex rounded-lg bg-ink px-4 py-2 text-sm font-semibold text-paper"
          >
            Continue to admin
          </Link>
        </section>
      </main>
    );
  }

  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      <SignUp />
    </div>
  );
}
