"use client";

import { signIn, useSession } from "next-auth/react";
import { useLocale, useTranslations } from "next-intl";

import { Link } from "@/i18n/navigation";
import { signOutFederated } from "@/lib/auth/federated-signout";

export function AuthControls() {
  const t = useTranslations("common");
  const locale = useLocale();
  const { data: session, status } = useSession();

  if (status === "loading") {
    return (
      <span className="rounded-md border border-line bg-raised px-3 py-2 text-sm text-slate-muted">
        {t("loading")}
      </span>
    );
  }

  if (!session) {
    return (
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={() => signIn("keycloak", { callbackUrl: `/${locale}/admin` })}
          className="rounded-md border border-line bg-raised px-3 py-2 text-sm font-medium text-ink"
        >
          {t("signIn")}
        </button>
        <Link
          href="/sign-up"
          className="rounded-md bg-ink px-3 py-2 text-sm font-medium text-canvas"
        >
          {t("signUp")}
        </Link>
      </div>
    );
  }

  return (
    <div className="flex items-center gap-2">
      <span className="hidden text-sm text-slate-muted sm:inline">
        {session.user?.email || session.user?.name || "Signed in"}
      </span>
      <button
        type="button"
        onClick={() => void signOutFederated(`/${locale}/sign-in`)}
        className="rounded-md border border-line bg-raised px-3 py-2 text-sm font-medium text-ink"
      >
        {t("signOut")}
      </button>
    </div>
  );
}
