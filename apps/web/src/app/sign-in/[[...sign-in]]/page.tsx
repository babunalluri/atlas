import { SignIn } from "@clerk/nextjs";
import Link from "next/link";

export default function SignInPage() {
  const publishableKey = process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY;
  const clerkReady =
    !!publishableKey && !publishableKey.includes("replace_me");

  if (!clerkReady) {
    return (
      <main className="flex min-h-screen items-center justify-center px-4">
        <section className="surface-panel max-w-md rounded-2xl p-8 text-center">
          <p className="text-xs font-semibold uppercase tracking-[0.14em] text-teal">
            Local development
          </p>
          <h1 className="mt-2 font-display text-3xl font-semibold">
            Authentication is not configured
          </h1>
          <p className="mt-3 text-sm text-slate-muted">
            Development authentication is enabled, so you can continue as the
            seeded tenant administrator.
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
      <SignIn />
    </div>
  );
}
