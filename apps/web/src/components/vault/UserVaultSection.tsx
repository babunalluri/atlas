"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Input, Label } from "@/components/ui/Field";
import { TrashIcon } from "@/components/ui/icons";
import {
  deleteAdminUserVaultEntry,
  listAdminUserVault,
  upsertAdminUserVaultEntry,
  type UserVaultEntry,
  type UserVaultKind,
} from "@/lib/api/admin";
import { useAgentOsToken } from "@/lib/auth/token";
import { formatRelative } from "@/lib/utils";

const NAME_RE = /^[a-zA-Z][a-zA-Z0-9_]{0,63}$/;

/** Compact vault editor for embedding on the user form (fixed target user). */
export function UserVaultSection({ userId }: { userId: string }) {
  const { getAccessToken } = useAgentOsToken();
  const [tab, setTab] = useState<UserVaultKind>("secret");
  const [entries, setEntries] = useState<UserVaultEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [value, setValue] = useState("");

  const refresh = useCallback(async () => {
    const token = await getAccessToken();
    setEntries(await listAdminUserVault(token, userId));
  }, [getAccessToken, userId]);

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
            reason instanceof Error ? reason.message : "Failed to load vault",
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
      await upsertAdminUserVaultEntry(token, userId, key, {
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

  async function onDelete(entryName: string) {
    if (!window.confirm(`Delete “${entryName}”?`)) return;
    setBusy(true);
    try {
      const token = await getAccessToken();
      await deleteAdminUserVaultEntry(token, userId, entryName);
      await refresh();
      setError(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Delete failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="rounded-xl border border-line bg-raised/40 p-3">
      <div className="mb-2">
        <h2 className="text-sm font-semibold">Secrets & Variables</h2>
        <p className="text-xs text-slate-muted">
          Personal tool keys for this user. Name and value are free-form; values
          are encrypted and never shown after save.
        </p>
      </div>

      <div className="flex gap-2 border-b border-line">
        {(["secret", "variable"] as const).map((kind) => (
          <button
            key={kind}
            type="button"
            onClick={() => setTab(kind)}
            className={`-mb-px border-b-2 px-3 py-1.5 text-xs font-medium ${
              tab === kind
                ? "border-ink text-ink"
                : "border-transparent text-slate-muted hover:text-ink"
            }`}
          >
            {kind === "secret" ? "Secrets" : "Variables"}
          </button>
        ))}
      </div>

      <div className="mt-3 grid gap-2 sm:grid-cols-[1fr_1.4fr_auto]">
        <div>
          <Label htmlFor="user-vault-name">Name</Label>
          <Input
            id="user-vault-name"
            value={name}
            placeholder={tab === "secret" ? "my_api_key" : "my_setting"}
            onChange={(event) => setName(event.target.value)}
          />
        </div>
        <div>
          <Label htmlFor="user-vault-value">Value</Label>
          <Input
            id="user-vault-value"
            value={value}
            placeholder="Enter value"
            onChange={(event) => setValue(event.target.value)}
          />
        </div>
        <div className="flex items-end">
          <Button
            size="sm"
            onClick={() => void onSave()}
            disabled={busy || loading}
          >
            {busy ? "Saving…" : "Save"}
          </Button>
        </div>
      </div>

      {error ? (
        <p className="mt-2 text-xs text-amber">{error}</p>
      ) : null}

      <ul className="mt-3 max-h-56 divide-y divide-line overflow-y-auto rounded-md border border-line">
        {loading ? (
          <li className="px-3 py-3 text-sm text-slate-muted">Loading…</li>
        ) : filtered.length === 0 ? (
          <li className="px-3 py-3 text-sm text-slate-muted">
            No {tab === "secret" ? "secrets" : "variables"} yet.
          </li>
        ) : (
          filtered.map((row) => (
            <li
              key={row.name}
              className="flex items-center justify-between gap-2 px-2.5 py-1.5"
            >
              <div className="min-w-0">
                <div className="flex items-center gap-1.5">
                  <span className="truncate font-mono text-sm">{row.name}</span>
                  <Badge tone="neutral">{row.kind}</Badge>
                </div>
                <p className="text-[11px] text-slate-muted">
                  Updated {formatRelative(row.updatedAt)} · value hidden
                </p>
              </div>
              <Button
                size="sm"
                variant="ghost"
                aria-label={`Delete ${row.name}`}
                disabled={busy}
                onClick={() => void onDelete(row.name)}
              >
                <TrashIcon />
              </Button>
            </li>
          ))
        )}
      </ul>
    </section>
  );
}
