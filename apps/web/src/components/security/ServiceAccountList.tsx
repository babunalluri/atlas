"use client";

import { Link } from "@/i18n/navigation";
import { useMemo, useState } from "react";

import { Badge } from "@/components/ui/Badge";
import { Button, buttonClassName } from "@/components/ui/Button";
import { Input } from "@/components/ui/Field";
import { PencilIcon, TrashIcon } from "@/components/ui/icons";
import {
  revokeServiceAccount,
  type ServiceAccountSummary,
} from "@/lib/api/admin";
import { useAgentOsToken } from "@/lib/auth/token";
import { cn, formatRelative } from "@/lib/utils";

type StatusFilter = "all" | "active" | "revoked";

const PAGE_SIZE = 25;

export function ServiceAccountList({
  initial,
}: {
  initial: ServiceAccountSummary[];
}) {
  const { getAccessToken } = useAgentOsToken();
  const [accounts, setAccounts] = useState(initial);
  const [q, setQ] = useState("");
  const [status, setStatus] = useState<StatusFilter>("all");
  const [page, setPage] = useState(1);
  const [revokingIds, setRevokingIds] = useState<Set<string>>(() => new Set());
  const [error, setError] = useState<string | null>(null);

  const filtered = useMemo(() => {
    const query = q.trim().toLowerCase();
    return accounts.filter((account) => {
      const revoked = Boolean(account.revokedAt);
      if (status === "active" && revoked) return false;
      if (status === "revoked" && !revoked) return false;
      if (!query) return true;
      const haystack = [account.name, account.tokenPrefix, ...account.scopes]
        .join(" ")
        .toLowerCase();
      return haystack.includes(query);
    });
  }, [accounts, q, status]);

  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const safePage = Math.min(page, totalPages);
  const start = (safePage - 1) * PAGE_SIZE;
  const pageItems = filtered.slice(start, start + PAGE_SIZE);
  const end = Math.min(filtered.length, start + PAGE_SIZE);

  async function onRevoke(account: ServiceAccountSummary) {
    if (account.revokedAt) return;
    if (
      !window.confirm(
        `Revoke “${account.name}”? Existing tokens stop working immediately.`,
      )
    ) {
      return;
    }
    setRevokingIds((current) => new Set(current).add(account.id));
    setError(null);
    try {
      await revokeServiceAccount(await getAccessToken(), account.id);
      const revokedAt = new Date().toISOString();
      setAccounts((current) =>
        current.map((item) =>
          item.id === account.id ? { ...item, revokedAt } : item,
        ),
      );
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Revoke failed");
    } finally {
      setRevokingIds((current) => {
        const next = new Set(current);
        next.delete(account.id);
        return next;
      });
    }
  }

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="font-display text-2xl font-semibold tracking-tight">
            Service accounts
          </h1>
          <p className="mt-0.5 text-sm text-slate-muted">
            Machine tokens for CI and MCP. Shown once — hashed at rest.
          </p>
        </div>
        <Link
          href="/admin/service-accounts/new"
          className={buttonClassName({ variant: "accent" })}
        >
          Create
        </Link>
      </header>
      {error ? <p className="text-sm text-rose">{error}</p> : null}

      <section className="table-shell rounded-xl">
        <div className="space-y-3 border-b border-line px-4 py-3">
          <div className="flex flex-wrap items-center gap-2">
            <Input
              value={q}
              placeholder="Search accounts…"
              className="min-w-[220px] flex-1"
              onChange={(event) => {
                setQ(event.target.value);
                setPage(1);
              }}
            />
            {(
              [
                ["all", "All"],
                ["active", "Active"],
                ["revoked", "Revoked"],
              ] as const
            ).map(([value, label]) => (
              <button
                key={value}
                type="button"
                onClick={() => {
                  setStatus(value);
                  setPage(1);
                }}
                className={cn(
                  "rounded-md border px-2.5 py-1.5 text-xs font-medium transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ink/20 focus-visible:ring-offset-1 focus-visible:ring-offset-canvas",
                  status === value
                    ? "border-line-strong bg-mist text-ink"
                    : "border-transparent bg-raised text-slate-muted hover:bg-mist",
                )}
              >
                {label}
              </button>
            ))}
          </div>
          <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-slate-muted">
            <p>
              {filtered.length === 0
                ? "No accounts match"
                : `Showing ${start + 1}–${end} of ${filtered.length} accounts`}
            </p>
            <div className="flex items-center gap-2">
              <Button
                size="sm"
                variant="secondary"
                disabled={safePage <= 1}
                onClick={() => setPage((current) => Math.max(1, current - 1))}
              >
                Previous
              </Button>
              <span className="mono-cell">
                {safePage} / {totalPages}
              </span>
              <Button
                size="sm"
                variant="secondary"
                disabled={safePage >= totalPages}
                onClick={() =>
                  setPage((current) => Math.min(totalPages, current + 1))
                }
              >
                Next
              </Button>
            </div>
          </div>
        </div>

        <div className="hidden items-center gap-3 border-b border-line px-4 py-2 md:flex">
          <div className="grid min-w-0 flex-1 grid-cols-[1.3fr_1.2fr_0.8fr_0.6fr] gap-3">
            <span className="th-label">Account</span>
            <span className="th-label">Scopes</span>
            <span className="th-label">Activity</span>
            <span className="th-label text-right">Status</span>
          </div>
          <span className="th-label w-auto shrink-0 text-right">Actions</span>
        </div>
        <ul>
          {pageItems.map((account) => (
            <li key={account.id} className="border-b border-line/60 last:border-0">
              <div className="flex items-center gap-3 px-4 py-2.5 transition hover:bg-mist/70">
                <Link
                  href={`/admin/service-accounts/${account.id}`}
                  className="grid min-w-0 flex-1 items-center gap-3 md:grid-cols-[1.3fr_1.2fr_0.8fr_0.6fr]"
                >
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium">{account.name}</p>
                    <p className="mono-cell truncate text-slate-muted">
                      {account.tokenPrefix}
                    </p>
                  </div>
                  <p className="truncate text-xs text-slate-muted">
                    {account.scopes.join(", ")}
                  </p>
                  <div className="text-xs text-slate-muted">
                    <p>Created {formatRelative(account.createdAt)}</p>
                    <p>
                      {account.lastUsedAt
                        ? `Used ${formatRelative(account.lastUsedAt)}`
                        : "Never used"}
                    </p>
                  </div>
                  <div className="md:justify-self-end">
                    <Badge
                      dot
                      tone={account.revokedAt ? "danger" : "success"}
                    >
                      {account.revokedAt ? "Revoked" : "Active"}
                    </Badge>
                  </div>
                </Link>
                <div className="flex shrink-0 items-center justify-end gap-0.5">
                  <Link
                    href={`/admin/service-accounts/${account.id}`}
                    className={buttonClassName({
                      variant: "ghost",
                      size: "icon",
                    })}
                    aria-label={`Edit ${account.name}`}
                    title="Edit"
                  >
                    <PencilIcon />
                  </Link>
                  {!account.revokedAt ? (
                    <Button
                      size="icon"
                      variant="danger"
                      aria-label={`Revoke ${account.name}`}
                      title="Revoke"
                      disabled={revokingIds.has(account.id)}
                      onClick={() => void onRevoke(account)}
                    >
                      {revokingIds.has(account.id) ? "…" : <TrashIcon />}
                    </Button>
                  ) : null}
                </div>
              </div>
            </li>
          ))}
          {filtered.length === 0 ? (
            <li className="px-4 py-10 text-center text-sm text-slate-muted">
              {accounts.length === 0
                ? "No service accounts yet — create one above."
                : "No accounts match this search."}
            </li>
          ) : null}
        </ul>
      </section>
    </div>
  );
}
