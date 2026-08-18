"use client";

import { useTranslations } from "next-intl";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Button } from "@/components/ui/Button";
import { Input, Label } from "@/components/ui/Field";
import { CloseIcon, PencilIcon, SaveIcon, TrashIcon } from "@/components/ui/icons";
import { formatApiError } from "@/lib/agentos/client";
import {
  deleteUserVaultEntry,
  listUserVault,
  upsertUserVaultEntry,
  type UserVaultEntry,
  type UserVaultKind,
} from "@/lib/api/admin";
import {
  getAccessToken as resolveAccessToken,
  useAgentOsToken,
} from "@/lib/auth/token";
import { cn, formatRelative } from "@/lib/utils";

const NAME_RE = /^[a-zA-Z][a-zA-Z0-9_]{0,63}$/;

function vaultError(reason: unknown, fallback: string): string {
  return formatApiError(reason, fallback);
}

/** Self-service secrets and variables for the signed-in user. */
export function UserSelfVaultEditor() {
  const t = useTranslations("common");
  const { isLoaded, isSignedIn } = useAgentOsToken();
  const [tab, setTab] = useState<UserVaultKind>("secret");
  const [entries, setEntries] = useState<UserVaultEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [value, setValue] = useState("");
  const [editingName, setEditingName] = useState<string | null>(null);
  const [editValue, setEditValue] = useState("");
  const initialLoadRef = useRef(true);

  const requireToken = useCallback(async () => {
    const token = await resolveAccessToken();
    if (!token) {
      throw new Error("Sign in required");
    }
    return token;
  }, []);

  const refresh = useCallback(async () => {
    const token = await requireToken();
    setEntries(await listUserVault(token));
  }, [requireToken]);

  useEffect(() => {
    if (!isLoaded || !isSignedIn) {
      setLoading(false);
      return;
    }
    let cancelled = false;
    const firstLoad = initialLoadRef.current;
    if (firstLoad) setLoading(true);
    void (async () => {
      try {
        await refresh();
        if (!cancelled) setError(null);
      } catch (reason) {
        if (!cancelled) {
          setError(vaultError(reason, "Failed to load settings"));
        }
      } finally {
        if (!cancelled && firstLoad) {
          initialLoadRef.current = false;
          setLoading(false);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [isLoaded, isSignedIn, refresh]);

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
      const token = await requireToken();
      await upsertUserVaultEntry(token, key, {
        value: value.trim(),
        kind: tab,
      });
      setName("");
      setValue("");
      await refresh();
      setError(null);
    } catch (reason) {
      setError(vaultError(reason, "Save failed"));
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
      const token = await requireToken();
      await upsertUserVaultEntry(token, row.name, {
        value: next,
        kind: row.kind,
      });
      cancelUpdate();
      await refresh();
      setError(null);
    } catch (reason) {
      setError(vaultError(reason, "Update failed"));
    } finally {
      setBusy(false);
    }
  }

  async function onDelete(entryName: string) {
    if (!window.confirm(`Delete “${entryName}”?`)) return;
    setBusy(true);
    try {
      const token = await requireToken();
      await deleteUserVaultEntry(token, entryName);
      if (editingName === entryName) cancelUpdate();
      await refresh();
      setError(null);
    } catch (reason) {
      setError(vaultError(reason, "Delete failed"));
    } finally {
      setBusy(false);
    }
  }

  if (!isLoaded || !isSignedIn) {
    return (
      <p className="text-sm text-slate-muted">{t("loading")}</p>
    );
  }

  return (
    <div>
      <div className="flex gap-2 border-b border-line">
        {(["secret", "variable"] as const).map((kind) => (
          <button
            key={kind}
            type="button"
            onClick={() => {
              setTab(kind);
              cancelUpdate();
            }}
            className={cn(
              "-mb-px border-b-2 px-3 py-1.5 text-sm font-medium",
              tab === kind
                ? "border-ink text-ink"
                : "border-transparent text-slate-muted hover:text-ink",
            )}
          >
            {kind === "secret" ? t("profile.secrets") : t("profile.variables")}
          </button>
        ))}
      </div>
      <div className="mt-4 grid gap-3 sm:grid-cols-2">
        <div>
          <Label htmlFor="vault-name">{t("profile.name")}</Label>
          <Input
            id="vault-name"
            value={name}
            autoComplete="off"
            placeholder={tab === "secret" ? "access_token" : "base_url"}
            onChange={(event) => setName(event.target.value)}
          />
        </div>
        <div>
          <Label htmlFor="vault-value">{t("profile.value")}</Label>
          <Input
            id="vault-value"
            type={tab === "secret" ? "password" : "text"}
            value={value}
            autoComplete="off"
            placeholder={t("profile.value")}
            onChange={(event) => setValue(event.target.value)}
          />
        </div>
      </div>
      <Button
        type="button"
        className="mt-3"
        size="sm"
        icon={<SaveIcon />}
        onClick={() => void onSave()}
        disabled={busy || loading}
      >
        {busy ? t("saving") : t("save")}
      </Button>
      {error ? <p className="mt-3 text-sm text-amber">{error}</p> : null}
      <ul className="mt-4 divide-y divide-line overflow-hidden rounded-lg border border-line">
        {loading ? (
          <li className="px-4 py-3 text-sm text-slate-muted">{t("loading")}</li>
        ) : filtered.length === 0 ? (
          <li className="px-4 py-3 text-sm text-slate-muted">
            {tab === "secret" ? t("profile.noSecrets") : t("profile.noVariables")}
          </li>
        ) : (
          filtered.map((row) => {
            const editing = editingName === row.name;
            return (
              <li key={row.name} className="px-4 py-3">
                <div className="flex items-center justify-between gap-3">
                  <div className="min-w-0">
                    <p className="truncate font-mono text-sm text-ink">{row.name}</p>
                    <p className="text-xs text-slate-muted">
                      {t("profile.savedHidden")} · {formatRelative(row.updatedAt)}
                    </p>
                  </div>
                  <div className="flex shrink-0 items-center">
                    <Button
                      type="button"
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
                      type="button"
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
                  <div className="mt-3 grid gap-2 sm:max-w-md">
                    <Input
                      id={`vault-update-${row.name}`}
                      type={row.kind === "secret" ? "password" : "text"}
                      value={editValue}
                      autoComplete="off"
                      placeholder={t("profile.newValue")}
                      onChange={(event) => setEditValue(event.target.value)}
                    />
                    <div className="flex gap-2">
                      <Button
                        type="button"
                        size="sm"
                        icon={<SaveIcon />}
                        onClick={() => void onUpdate(row)}
                        disabled={busy || !editValue.trim()}
                      >
                        {busy ? t("saving") : t("save")}
                      </Button>
                      <Button
                        type="button"
                        size="sm"
                        variant="secondary"
                        icon={<CloseIcon />}
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
