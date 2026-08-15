"use client";

import { useLocale, useTranslations } from "next-intl";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { UserIdentityChip } from "@/components/auth/UserIdentityChip";
import { LanguageSwitcher } from "@/components/i18n/LanguageSwitcher";
import { Button } from "@/components/ui/Button";
import { Input, Label } from "@/components/ui/Field";
import { ChevronDownIcon, PencilIcon, TrashIcon } from "@/components/ui/icons";
import {
  deleteUserVaultEntry,
  listUserVault,
  upsertUserVaultEntry,
  type UserVaultEntry,
  type UserVaultKind,
} from "@/lib/api/admin";
import { signOutFederated } from "@/lib/auth/federated-signout";
import { useAgentOsToken } from "@/lib/auth/token";
import type { IdentityUser } from "@/lib/auth/user-identity";
import { cn, formatRelative } from "@/lib/utils";

const NAME_RE = /^[a-zA-Z][a-zA-Z0-9_]{0,63}$/;

export function WorkspaceProfileMenu({
  user,
}: {
  user?: IdentityUser | null;
}) {
  const t = useTranslations("common");
  const locale = useLocale();
  const { getAccessToken } = useAgentOsToken();
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function onPointerDown(event: MouseEvent) {
      if (!rootRef.current?.contains(event.target as Node)) {
        setOpen(false);
      }
    }
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div ref={rootRef} className="relative min-w-0">
      <button
        type="button"
        aria-expanded={open}
        aria-haspopup="dialog"
        aria-label={t("profile.open")}
        onClick={() => setOpen((value) => !value)}
        className="flex min-w-0 items-center gap-1 rounded-md border border-transparent px-1 py-0.5 transition hover:border-line hover:bg-raised/70"
      >
        <UserIdentityChip user={user} compact />
        <ChevronDownIcon className="size-3.5 shrink-0 text-slate-muted" />
      </button>
      {open ? (
        <div
          role="dialog"
          aria-label={t("profile.settings")}
          className="absolute right-0 z-50 mt-2 w-[min(20.5rem,calc(100vw-2rem))] overflow-hidden rounded-xl border border-line bg-canvas shadow-lg"
        >
          <div className="border-b border-line px-3 py-2">
            <p className="text-xs font-semibold uppercase tracking-[0.12em] text-slate-muted">
              {t("profile.settings")}
            </p>
          </div>
          <div className="max-h-[min(28rem,70vh)] space-y-3 overflow-y-auto px-3 py-3">
            <ProfileVaultEditor getAccessToken={getAccessToken} />
            <LanguageSwitcher labeled className="block" />
          </div>
          <div className="border-t border-line px-3 py-2">
            <Button
              type="button"
              size="sm"
              variant="secondary"
              className="w-full"
              onClick={() => void signOutFederated(`/${locale}`)}
            >
              {t("signOut")}
            </Button>
          </div>
        </div>
      ) : null}
    </div>
  );
}

function ProfileVaultEditor({
  getAccessToken,
}: {
  getAccessToken: () => Promise<string>;
}) {
  const t = useTranslations("common");
  const [tab, setTab] = useState<UserVaultKind>("secret");
  const [entries, setEntries] = useState<UserVaultEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [value, setValue] = useState("");
  const [editingName, setEditingName] = useState<string | null>(null);
  const [editValue, setEditValue] = useState("");

  const refresh = useCallback(async () => {
    const token = await getAccessToken();
    setEntries(await listUserVault(token));
  }, [getAccessToken]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    void (async () => {
      try {
        await refresh();
        if (!cancelled) setError(null);
      } catch (reason) {
        if (!cancelled) {
          setError(
            reason instanceof Error ? reason.message : "Failed to load settings",
          );
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [refresh]);

  const filtered = useMemo(
    () => entries.filter((row) => row.kind === tab),
    [entries, tab],
  );

  async function onSave() {
    const key = name.trim();
    if (!NAME_RE.test(key)) {
      setError("Name must match ^[a-zA-Z][a-zA-Z0-9_]{0,63}$");
      return;
    }
    if (!value.trim()) {
      setError("Value is required");
      return;
    }
    setBusy(true);
    try {
      const token = await getAccessToken();
      await upsertUserVaultEntry(token, key, {
        value: value.trim(),
        kind: tab,
      });
      setName("");
      setValue("");
      await refresh();
      setError(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Save failed");
    } finally {
      setBusy(false);
    }
  }

  function beginUpdate(entryName: string) {
    setEditingName(entryName);
    setEditValue("");
    setError(null);
  }

  function cancelUpdate() {
    setEditingName(null);
    setEditValue("");
  }

  async function onUpdate(row: UserVaultEntry) {
    const next = editValue.trim();
    if (!next) return;
    setBusy(true);
    try {
      const token = await getAccessToken();
      await upsertUserVaultEntry(token, row.name, {
        value: next,
        kind: row.kind,
      });
      cancelUpdate();
      await refresh();
      setError(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Update failed");
    } finally {
      setBusy(false);
    }
  }

  async function onDelete(entryName: string) {
    if (!window.confirm(`Delete “${entryName}”?`)) return;
    setBusy(true);
    try {
      const token = await getAccessToken();
      await deleteUserVaultEntry(token, entryName);
      if (editingName === entryName) cancelUpdate();
      await refresh();
      setError(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Delete failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <p className="text-xs leading-relaxed text-slate-muted">
        {t("profile.vaultHint")}
      </p>
      <div className="mt-2 flex gap-2 border-b border-line">
        {(["secret", "variable"] as const).map((kind) => (
          <button
            key={kind}
            type="button"
            onClick={() => {
              setTab(kind);
              cancelUpdate();
            }}
            className={cn(
              "-mb-px border-b-2 px-2 py-1 text-[11px] font-medium",
              tab === kind
                ? "border-ink text-ink"
                : "border-transparent text-slate-muted hover:text-ink",
            )}
          >
            {kind === "secret" ? t("profile.secrets") : t("profile.variables")}
          </button>
        ))}
      </div>
      <div className="mt-2 grid gap-2">
        <div>
          <Label htmlFor="profile-vault-name">{t("profile.name")}</Label>
          <Input
            id="profile-vault-name"
            value={name}
            autoComplete="off"
            placeholder={tab === "secret" ? "access_token" : "base_url"}
            onChange={(event) => setName(event.target.value)}
          />
        </div>
        <div>
          <Label htmlFor="profile-vault-value">{t("profile.value")}</Label>
          <Input
            id="profile-vault-value"
            type={tab === "secret" ? "password" : "text"}
            value={value}
            autoComplete="off"
            placeholder={t("profile.value")}
            onChange={(event) => setValue(event.target.value)}
          />
        </div>
        <Button
          size="sm"
          onClick={() => void onSave()}
          disabled={busy || loading}
        >
          {busy ? t("saving") : t("save")}
        </Button>
      </div>
      {error ? <p className="mt-2 text-xs text-amber">{error}</p> : null}
      <ul className="mt-2 max-h-48 divide-y divide-line overflow-y-auto rounded-md border border-line">
        {loading ? (
          <li className="px-2.5 py-2 text-xs text-slate-muted">{t("loading")}</li>
        ) : filtered.length === 0 ? (
          <li className="px-2.5 py-2 text-xs text-slate-muted">
            {tab === "secret" ? t("profile.noSecrets") : t("profile.noVariables")}
          </li>
        ) : (
          filtered.map((row) => {
            const editing = editingName === row.name;
            return (
              <li key={row.name} className="px-2 py-1.5">
                <div className="flex items-center justify-between gap-2">
                  <div className="min-w-0">
                    <p className="truncate font-mono text-xs text-ink">
                      {row.name}
                    </p>
                    <p className="text-[10px] text-slate-muted">
                      {t("profile.savedHidden")} · {formatRelative(row.updatedAt)}
                    </p>
                  </div>
                  <div className="flex shrink-0 items-center">
                    <Button
                      size="icon"
                      variant="ghost"
                      aria-label={`${t("profile.update")} ${row.name}`}
                      title={t("profile.update")}
                      disabled={busy}
                      onClick={() =>
                        editing ? cancelUpdate() : beginUpdate(row.name)
                      }
                    >
                      <PencilIcon />
                    </Button>
                    <Button
                      size="icon"
                      variant="ghost"
                      aria-label={`Delete ${row.name}`}
                      disabled={busy}
                      onClick={() => void onDelete(row.name)}
                    >
                      <TrashIcon />
                    </Button>
                  </div>
                </div>
                {editing ? (
                  <div className="mt-1.5 grid gap-1.5">
                    <Input
                      id={`profile-vault-update-${row.name}`}
                      type={tab === "secret" ? "password" : "text"}
                      value={editValue}
                      autoComplete="off"
                      placeholder={t("profile.newValue")}
                      className="px-2 py-1.5 text-xs"
                      onChange={(event) => setEditValue(event.target.value)}
                    />
                    <div className="flex gap-1.5">
                      <Button
                        size="sm"
                        onClick={() => void onUpdate(row)}
                        disabled={busy || !editValue.trim()}
                      >
                        {busy ? t("saving") : t("save")}
                      </Button>
                      <Button
                        size="sm"
                        variant="secondary"
                        disabled={busy}
                        onClick={cancelUpdate}
                      >
                        {t("cancel")}
                      </Button>
                    </div>
                  </div>
                ) : null}
              </li>
            );
          })
        )}
      </ul>
    </div>
  );
}
