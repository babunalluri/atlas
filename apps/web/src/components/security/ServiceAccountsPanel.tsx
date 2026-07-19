"use client";

import { useState } from "react";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Input, Label } from "@/components/ui/Field";
import {
  createServiceAccount,
  revokeServiceAccount,
  type ServiceAccountSummary,
} from "@/lib/api/admin";
import { useAgentOsToken } from "@/lib/auth/token";
import { formatRelative } from "@/lib/utils";

const SCOPE_OPTIONS = [
  ["agents:run", "Run agents"],
  ["teams:run", "Run teams"],
  ["sessions:read", "Read sessions"],
  ["traces:read", "Read traces"],
  ["mcp:access", "Connect to MCP"],
  ["mcp:read", "Discover MCP resources"],
  ["mcp:run", "Run through MCP"],
  ["mcp:sessions:read", "Read sessions through MCP"],
] as const;

export function ServiceAccountsPanel({
  initial,
}: {
  initial: ServiceAccountSummary[];
}) {
  const [accounts, setAccounts] = useState(initial);
  const [name, setName] = useState("");
  const [scopes, setScopes] = useState<string[]>(["agents:run"]);
  const [expiresAt, setExpiresAt] = useState("");
  const [createdToken, setCreatedToken] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { getAccessToken } = useAgentOsToken();

  function toggleScope(scope: string) {
    setScopes((current) =>
      current.includes(scope)
        ? current.filter((item) => item !== scope)
        : [...current, scope],
    );
  }

  async function create() {
    if (!name.trim() || scopes.length === 0) return;
    setBusy(true);
    setError(null);
    try {
      const account = await createServiceAccount(await getAccessToken(), {
        name: name.trim(),
        scopes,
        expiresAt: expiresAt ? new Date(expiresAt).toISOString() : null,
      });
      setAccounts((current) => [account, ...current]);
      setCreatedToken(account.token);
      setName("");
      setExpiresAt("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Creation failed");
    } finally {
      setBusy(false);
    }
  }

  async function revoke(accountId: string) {
    setBusy(true);
    setError(null);
    try {
      await revokeServiceAccount(await getAccessToken(), accountId);
      setAccounts((current) =>
        current.map((account) =>
          account.id === accountId
            ? { ...account, revokedAt: new Date().toISOString() }
            : account,
        ),
      );
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Revocation failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-6">
      <header>
        <p className="text-xs font-semibold uppercase tracking-[0.14em] text-teal">
          Security
        </p>
        <h1 className="font-display text-4xl font-semibold tracking-tight">
          Service accounts
        </h1>
        <p className="mt-2 max-w-2xl text-sm text-slate-muted">
          Issue scoped machine credentials for CI, MCP clients, and integrations.
          Tokens are hashed at rest and shown only once.
        </p>
      </header>

      {createdToken ? (
        <section className="rounded-2xl border border-teal/30 bg-raised/70 p-5 shadow-[0_18px_50px_-36px_rgba(15,143,123,0.8)] backdrop-blur">
          <p className="text-sm font-semibold">Copy this token now</p>
          <p className="mt-1 text-xs text-slate-muted">
            It cannot be retrieved after this message is dismissed.
          </p>
          <div className="mt-3 flex flex-col gap-2 sm:flex-row">
            <code className="min-w-0 flex-1 overflow-x-auto rounded-lg bg-[#071018] px-3 py-2.5 font-mono text-xs text-[#d7e0e8]">
              {createdToken}
            </code>
            <Button
              variant="accent"
              onClick={() => navigator.clipboard.writeText(createdToken)}
            >
              Copy token
            </Button>
            <Button variant="ghost" onClick={() => setCreatedToken(null)}>
              Dismiss
            </Button>
          </div>
        </section>
      ) : null}

      <section className="surface-panel rounded-2xl p-5">
        <div className="grid gap-5 lg:grid-cols-[1fr_1.5fr_1fr_auto] lg:items-end">
          <div>
            <Label htmlFor="service-account-name">Name</Label>
            <Input
              id="service-account-name"
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="Production CI"
            />
          </div>
          <fieldset>
            <legend className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-slate-muted">
              Least-privilege scopes
            </legend>
            <div className="flex flex-wrap gap-2">
              {SCOPE_OPTIONS.map(([scope, label]) => (
                <label
                  key={scope}
                  className="flex cursor-pointer items-center gap-2 rounded-full border border-line bg-raised/75 px-3 py-2 text-xs"
                >
                  <input
                    type="checkbox"
                    checked={scopes.includes(scope)}
                    onChange={() => toggleScope(scope)}
                    className="accent-teal"
                  />
                  {label}
                </label>
              ))}
            </div>
          </fieldset>
          <div>
            <Label htmlFor="service-account-expiry">Expires (optional)</Label>
            <Input
              id="service-account-expiry"
              type="datetime-local"
              value={expiresAt}
              onChange={(event) => setExpiresAt(event.target.value)}
            />
          </div>
          <Button
            variant="accent"
            disabled={busy || !name.trim() || scopes.length === 0}
            onClick={create}
          >
            {busy ? "Working…" : "Issue token"}
          </Button>
        </div>
        {error ? <p className="mt-3 text-sm text-rose">{error}</p> : null}
      </section>

      <ul className="table-shell rounded-xl">
        {accounts.length === 0 ? (
          <li className="px-5 py-10 text-center text-sm text-slate-muted">
            No service accounts yet.
          </li>
        ) : null}
        {accounts.map((account) => (
          <li
            key={account.id}
            className="flex flex-col gap-4 border-b border-line px-5 py-4 last:border-0 md:flex-row md:items-center md:justify-between"
          >
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <p className="font-medium">{account.name}</p>
                <Badge dot tone={account.revokedAt ? "danger" : "success"}>
                  {account.revokedAt ? "Revoked" : "Active"}
                </Badge>
              </div>
              <p className="mt-1 font-mono text-xs text-slate-muted">
                {account.tokenPrefix}
              </p>
              <div className="mt-2 flex flex-wrap gap-1.5">
                {account.scopes.map((scope) => (
                  <Badge key={scope} tone="info">
                    {scope}
                  </Badge>
                ))}
              </div>
            </div>
            <div className="flex items-center gap-4 md:text-right">
              <div className="text-xs text-slate-muted">
                <p>Created {formatRelative(account.createdAt)}</p>
                <p>
                  {account.lastUsedAt
                    ? `Used ${formatRelative(account.lastUsedAt)}`
                    : "Never used"}
                </p>
              </div>
              {!account.revokedAt ? (
                <Button
                  variant="danger"
                  disabled={busy}
                  onClick={() => revoke(account.id)}
                >
                  Revoke
                </Button>
              ) : null}
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
