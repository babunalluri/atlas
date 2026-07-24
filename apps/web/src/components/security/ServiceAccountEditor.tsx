"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import { BackLink } from "@/components/ui/BackLink";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { EditorActions } from "@/components/ui/EditorActions";
import { Input, Label } from "@/components/ui/Field";
import { TrashIcon } from "@/components/ui/icons";
import {
  createServiceAccount,
  revokeServiceAccount,
  type ServiceAccountSummary,
} from "@/lib/api/admin";
import { useAgentOsToken } from "@/lib/auth/token";
import { cn, formatRelative } from "@/lib/utils";

const SCOPE_GROUPS = [
  {
    label: "Run",
    scopes: [
      ["agents:run", "Agents"],
      ["teams:run", "Teams"],
      ["workflows:run", "Workflows"],
    ],
  },
  {
    label: "Read",
    scopes: [
      ["sessions:read", "Sessions"],
      ["traces:read", "Traces"],
    ],
  },
  {
    label: "MCP",
    scopes: [
      ["mcp:access", "Connect"],
      ["mcp:read", "Discover"],
      ["mcp:run", "Run"],
      ["mcp:sessions:read", "Sessions"],
    ],
  },
] as const;

const PRESETS: Array<{ id: string; label: string; scopes: string[] }> = [
  {
    id: "runner",
    label: "Runner",
    scopes: ["agents:run", "teams:run", "workflows:run"],
  },
  {
    id: "mcp",
    label: "MCP client",
    scopes: ["mcp:access", "mcp:read", "mcp:run", "mcp:sessions:read"],
  },
  {
    id: "readonly",
    label: "Read-only",
    scopes: ["sessions:read", "traces:read"],
  },
];

function toExpiresIso(localValue: string): string | null {
  if (!localValue.trim()) return null;
  const date = new Date(localValue);
  if (Number.isNaN(date.getTime())) {
    throw new Error("Expires must be a valid date and time");
  }
  if (date.getTime() <= Date.now()) {
    throw new Error("Expires must be in the future");
  }
  return date.toISOString();
}

export function ServiceAccountEditor({
  mode,
  initial,
}: {
  mode: "create" | "edit";
  initial?: ServiceAccountSummary;
}) {
  const router = useRouter();
  const { getAccessToken } = useAgentOsToken();
  const [name, setName] = useState(initial?.name ?? "");
  const [scopes, setScopes] = useState<string[]>(
    initial?.scopes ?? ["agents:run"],
  );
  const [expiresAt, setExpiresAt] = useState("");
  const [createdToken, setCreatedToken] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [account, setAccount] = useState(initial ?? null);
  const [busy, setBusy] = useState<"create" | "revoke" | null>(null);
  const [error, setError] = useState<string | null>(null);

  function toggleScope(scope: string) {
    setScopes((current) =>
      current.includes(scope)
        ? current.filter((item) => item !== scope)
        : [...current, scope],
    );
  }

  async function onCreate(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    const trimmed = name.trim();
    if (!trimmed) {
      setError("Name is required");
      return;
    }
    if (scopes.length === 0) {
      setError("Select at least one scope");
      return;
    }
    setBusy("create");
    setError(null);
    setCopied(false);
    try {
      const created = await createServiceAccount(await getAccessToken(), {
        name: trimmed,
        scopes,
        expiresAt: toExpiresIso(expiresAt),
      });
      setCreatedToken(created.token);
      setAccount(created);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Creation failed");
    } finally {
      setBusy(null);
    }
  }

  async function onRevoke() {
    if (!account || account.revokedAt) return;
    if (
      !window.confirm(
        `Revoke “${account.name}”? Existing tokens stop working immediately.`,
      )
    ) {
      return;
    }
    setBusy("revoke");
    setError(null);
    try {
      await revokeServiceAccount(await getAccessToken(), account.id);
      setAccount({ ...account, revokedAt: new Date().toISOString() });
      router.refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Revoke failed");
    } finally {
      setBusy(null);
    }
  }

  async function copyToken() {
    if (!createdToken) return;
    try {
      await navigator.clipboard.writeText(createdToken);
      setCopied(true);
    } catch {
      setError("Could not copy — select the token and copy manually");
    }
  }

  if (mode === "create" && !createdToken) {
    return (
      <div className="space-y-3">
        <header className="flex flex-wrap items-center justify-between gap-3">
          <div className="min-w-0">
            <p className="text-xs text-slate-muted">
              <Link href="/admin/service-accounts" className="hover:text-ink">
                Service accounts
              </Link>
              <span className="mx-1.5">/</span>
              New
            </p>
            <div className="flex min-w-0 items-center gap-1.5">
              <BackLink
                href="/admin/service-accounts"
                label="Back to service accounts"
              />
              <h1 className="font-display text-2xl font-semibold tracking-tight">
                New service account
              </h1>
            </div>
            <p className="mt-1 text-xs text-slate-muted">
              Pick scopes, then issue a token. The secret is shown once.
            </p>
          </div>
        </header>

        {error ? (
          <p className="rounded-md border border-rose/30 bg-rose/10 px-3 py-1.5 text-sm text-rose">
            {error}
          </p>
        ) : null}

        <form
          onSubmit={(event) => void onCreate(event)}
          className="rounded-xl border border-line bg-raised/40 p-3"
        >
          <div className="grid gap-3 sm:grid-cols-[minmax(0,1.2fr)_minmax(0,0.8fr)_auto] sm:items-end">
            <div>
              <Label htmlFor="service-account-name">Name</Label>
              <Input
                id="service-account-name"
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder="Production CI"
                autoComplete="off"
              />
            </div>
            <div>
              <Label htmlFor="service-account-expiry">Expires (optional)</Label>
              <Input
                id="service-account-expiry"
                type="datetime-local"
                value={expiresAt}
                onChange={(event) => setExpiresAt(event.target.value)}
              />
            </div>
            <Button type="submit" variant="accent" disabled={busy !== null}>
              {busy === "create" ? "Issuing…" : "Issue token"}
            </Button>
          </div>

          <div className="mt-3 flex flex-wrap items-center gap-2">
            <span className="text-xs text-slate-muted">Presets</span>
            {PRESETS.map((preset) => {
              const selected =
                preset.scopes.length === scopes.length &&
                preset.scopes.every((scope) => scopes.includes(scope));
              return (
                <button
                  key={preset.id}
                  type="button"
                  onClick={() => setScopes(preset.scopes)}
                  className={cn(
                    "rounded-md px-2.5 py-1 text-xs font-medium transition",
                    selected
                      ? "bg-ink text-canvas"
                      : "bg-raised text-slate-muted hover:bg-mist",
                  )}
                >
                  {preset.label}
                </button>
              );
            })}
          </div>

          <div className="mt-3 grid gap-3 sm:grid-cols-3">
            {SCOPE_GROUPS.map((group) => (
              <div
                key={group.label}
                className="rounded-md border border-line bg-canvas/30 p-2.5"
              >
                <p className="mb-1.5 text-[10px] font-semibold uppercase tracking-wide text-slate-muted">
                  {group.label}
                </p>
                <div className="space-y-1.5">
                  {group.scopes.map(([scope, label]) => (
                    <label
                      key={scope}
                      className="flex cursor-pointer items-center gap-2 text-sm"
                    >
                      <input
                        type="checkbox"
                        checked={scopes.includes(scope)}
                        onChange={() => toggleScope(scope)}
                        className="size-3.5 accent-teal"
                      />
                      <span className="min-w-0 truncate">{label}</span>
                    </label>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </form>
      </div>
    );
  }

  if (!account) {
    return (
      <p className="text-sm text-slate-muted">Service account not found.</p>
    );
  }

  return (
    <div className="space-y-3">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <p className="text-xs text-slate-muted">
            <Link href="/admin/service-accounts" className="hover:text-ink">
              Service accounts
            </Link>
            <span className="mx-1.5">/</span>
            {account.name}
          </p>
          <div className="flex min-w-0 items-center gap-1.5">
            <BackLink
              href="/admin/service-accounts"
              label="Back to service accounts"
            />
            <h1 className="truncate font-display text-2xl font-semibold tracking-tight">
              {account.name}
            </h1>
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-slate-muted">
            <Badge dot tone={account.revokedAt ? "danger" : "success"}>
              {account.revokedAt ? "Revoked" : "Active"}
            </Badge>
            <span className="mono-cell">{account.tokenPrefix}</span>
            <span>Created {formatRelative(account.createdAt)}</span>
          </div>
        </div>
        <EditorActions>
          {!account.revokedAt ? (
            <Button
              variant="danger"
              size="sm"
              disabled={busy !== null}
              onClick={() => void onRevoke()}
            >
              <TrashIcon />
              {busy === "revoke" ? "Revoking…" : "Revoke"}
            </Button>
          ) : null}
        </EditorActions>
      </header>

      {createdToken ? (
        <section className="rounded-xl border border-teal/40 bg-teal/10 p-3">
          <div className="flex flex-wrap items-start justify-between gap-2">
            <div>
              <p className="text-sm font-semibold">Copy this token now</p>
              <p className="text-xs text-slate-muted">
                It cannot be retrieved after you leave this page.
              </p>
            </div>
            <div className="flex gap-2">
              <Button
                type="button"
                variant="accent"
                size="sm"
                onClick={() => void copyToken()}
              >
                {copied ? "Copied" : "Copy"}
              </Button>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={() => {
                  setCreatedToken(null);
                  setCopied(false);
                  if (account) {
                    router.push(`/admin/service-accounts/${account.id}`);
                  }
                }}
              >
                Done
              </Button>
            </div>
          </div>
          <code className="mt-2 block overflow-x-auto rounded-md bg-[#071018] px-3 py-2 font-mono text-xs text-[#d7e0e8]">
            {createdToken}
          </code>
        </section>
      ) : null}

      {error ? (
        <p className="rounded-md border border-rose/30 bg-rose/10 px-3 py-1.5 text-sm text-rose">
          {error}
        </p>
      ) : null}

      <section className="rounded-xl border border-line bg-raised/40 p-3">
        <div className="grid gap-3 sm:grid-cols-2">
          <div>
            <p className="th-label">Token prefix</p>
            <p className="mono-cell mt-1 text-sm">{account.tokenPrefix}</p>
          </div>
          <div>
            <p className="th-label">Created by</p>
            <p className="mt-1 text-sm">{account.createdBy}</p>
          </div>
          <div>
            <p className="th-label">Last used</p>
            <p className="mt-1 text-sm text-slate-muted">
              {account.lastUsedAt
                ? formatRelative(account.lastUsedAt)
                : "Never"}
            </p>
          </div>
          <div>
            <p className="th-label">Expires</p>
            <p className="mt-1 text-sm text-slate-muted">
              {account.expiresAt
                ? formatRelative(account.expiresAt)
                : "No expiry"}
            </p>
          </div>
        </div>
        <div className="mt-3">
          <p className="th-label mb-1.5">Scopes</p>
          <div className="flex flex-wrap gap-1">
            {account.scopes.map((scope) => (
              <span
                key={scope}
                className="rounded bg-raised px-1.5 py-0.5 font-mono text-[10px] text-slate-muted"
              >
                {scope}
              </span>
            ))}
          </div>
        </div>
      </section>
    </div>
  );
}
