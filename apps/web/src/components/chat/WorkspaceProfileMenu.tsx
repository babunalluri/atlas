"use client";

import { useTranslations } from "next-intl";
import { useState } from "react";

import { UserIdentityChip } from "@/components/auth/UserIdentityChip";
import { WorkspaceSettingsBody } from "@/components/chat/WorkspaceSettingsBody";
import { Modal } from "@/components/ui/Modal";
import { SettingsGearIcon } from "@/components/ui/icons";
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
  const [settingsOpen, setSettingsOpen] = useState(false);

  return (
    <div className="flex min-w-0 items-center gap-1">
      {/* Settings opens over the current surface: the desk keeps its streams
          and the trader keeps their place, which navigating away would lose. */}
      <button
        type="button"
        onClick={() => setSettingsOpen(true)}
        title={t("settings.title")}
        aria-label={t("settings.title")}
        aria-haspopup="dialog"
        className="inline-flex size-8 shrink-0 items-center justify-center rounded-md border border-line bg-raised/70 text-slate-muted transition hover:border-line-strong hover:text-ink"
      >
        <SettingsGearIcon className="size-4" />
      </button>
      <div className="min-w-0 rounded-md border border-transparent px-1 py-0.5">
        <UserIdentityChip user={user} compact />
      </div>
      {settingsOpen ? (
        <Modal
          title={t("settings.title")}
          onClose={() => setSettingsOpen(false)}
          className="max-h-[92vh] max-w-3xl"
        >
          <WorkspaceSettingsBody tenantSlug={tenantSlug} showPageHeading={false} />
        </Modal>
      ) : null}
    </div>
  );
}
