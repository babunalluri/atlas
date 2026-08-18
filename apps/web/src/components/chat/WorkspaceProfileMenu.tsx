"use client";

import { useTranslations } from "next-intl";

import { UserIdentityChip } from "@/components/auth/UserIdentityChip";
import { SettingsGearIcon } from "@/components/ui/icons";
import { Link } from "@/i18n/navigation";
import type { IdentityUser } from "@/lib/auth/user-identity";

/** Inline profile chip + settings gear — no dropdown. */
export function WorkspaceProfileMenu({
  user,
  tenantSlug,
}: {
  user?: IdentityUser | null;
  tenantSlug: string;
}) {
  const t = useTranslations("common");

  return (
    <div className="flex min-w-0 items-center gap-1">
      <Link
        href={`/t/${tenantSlug}/settings`}
        title={t("settings.title")}
        aria-label={t("settings.title")}
        className="inline-flex size-8 shrink-0 items-center justify-center rounded-md border border-line bg-raised/70 text-slate-muted transition hover:border-line-strong hover:text-ink"
      >
        <SettingsGearIcon className="size-4" />
      </Link>
      <div className="min-w-0 rounded-md border border-transparent px-1 py-0.5">
        <UserIdentityChip user={user} compact />
      </div>
    </div>
  );
}
