"use client";

import { useState, type CSSProperties } from "react";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { resolveApproval } from "@/lib/api/admin";
import type { ApprovalRequest } from "@/lib/api/types";
import { useAgentOsToken } from "@/lib/auth/token";
import { formatRelative } from "@/lib/utils";

export function ApprovalsPanel({
  initial,
}: {
  initial: ApprovalRequest[];
}) {
  const { getAccessToken } = useAgentOsToken();
  const [items, setItems] = useState(initial);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [reasons, setReasons] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);

  async function decide(id: string, decision: "approved" | "rejected") {
    setBusyId(id);
    setError(null);
    try {
      const token = await getAccessToken();
      const updated = await resolveApproval(
        token,
        id,
        decision,
        reasons[id]?.trim() || undefined,
      );
      setItems((prev) => prev.map((item) => (item.id === id ? updated : item)));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Decision failed");
    } finally {
      setBusyId(null);
    }
  }

  const pendingCount = items.filter((item) => item.status === "pending").length;
  const approvedCount = items.filter((item) => item.status === "approved").length;
  const rejectedCount = items.filter((item) => item.status === "rejected").length;

  return (
    <div className="space-y-6">
      <header>
        <p className="text-xs font-semibold uppercase tracking-[0.14em] text-teal">
          Governance
        </p>
        <h1 className="font-display text-4xl font-semibold tracking-tight">
          Approvals
        </h1>
        <p className="mt-2 max-w-2xl text-sm text-slate-muted">
          Mutating tools pause until a tenant or platform admin resolves them.
          End users can initiate but cannot approve.
        </p>
      </header>
      {error ? <p className="text-sm text-rose">{error}</p> : null}

      <div className="grid gap-3 sm:grid-cols-3">
        <div className="kpi-card px-4 py-3" style={{ "--kpi-edge": "var(--tone-warning)" } as CSSProperties}>
          <p className="th-label flex items-center gap-1.5">
            {pendingCount > 0 ? <span className="live-dot text-amber" aria-hidden /> : null}
            Pending
          </p>
          <p className="mono-cell mt-1 text-2xl font-semibold text-ink">{pendingCount}</p>
        </div>
        <div className="kpi-card px-4 py-3" style={{ "--kpi-edge": "var(--tone-accent)" } as CSSProperties}>
          <p className="th-label">Approved</p>
          <p className="mono-cell mt-1 text-2xl font-semibold text-ink">{approvedCount}</p>
        </div>
        <div className="kpi-card px-4 py-3" style={{ "--kpi-edge": "var(--tone-danger)" } as CSSProperties}>
          <p className="th-label">Rejected</p>
          <p className="mono-cell mt-1 text-2xl font-semibold text-ink">{rejectedCount}</p>
        </div>
      </div>

      <ul className="space-y-3">
        {items.map((item) => (
          <li
            key={item.id}
            className="surface-panel rounded-xl p-4"
          >
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <div className="flex flex-wrap items-center gap-2">
                  <h2 className="font-display text-lg font-semibold">
                    {item.summary}
                  </h2>
                  <Badge
                    dot
                    live={item.status === "pending"}
                    tone={
                      item.status === "pending"
                        ? "warning"
                        : item.status === "approved"
                          ? "success"
                          : item.status === "rejected"
                            ? "danger"
                            : "neutral"
                    }
                  >
                    {item.status}
                  </Badge>
                </div>
                <p className="mono-cell mt-1 text-slate-muted">
                  {item.agentName} · {item.toolLabel} · {formatRelative(item.createdAt)}
                </p>
              </div>
              {item.status === "pending" ? (
                <div className="flex gap-2">
                  <Button
                    variant="accent"
                    disabled={busyId === item.id}
                    onClick={() => void decide(item.id, "approved")}
                  >
                    Approve
                  </Button>
                  <Button
                    variant="danger"
                    disabled={
                      busyId === item.id || !(reasons[item.id] ?? "").trim()
                    }
                    onClick={() => void decide(item.id, "rejected")}
                  >
                    Reject
                  </Button>
                </div>
              ) : null}
            </div>
            <pre className="mt-3 overflow-x-auto rounded-lg border border-line/60 bg-[#071018] px-4 py-3 font-mono text-xs leading-relaxed text-[#d7e0e8]">
              {JSON.stringify(item.argumentsPreview, null, 2)}
            </pre>
            {item.continuationError ? (
              <p className="mt-2 text-sm text-rose">
                {item.continuationError}
              </p>
            ) : null}
            {item.status === "pending" ? (
              <textarea
                aria-label={`Decision reason for ${item.summary}`}
                value={reasons[item.id] ?? ""}
                onChange={(event) =>
                  setReasons((current) => ({
                    ...current,
                    [item.id]: event.target.value,
                  }))
                }
                maxLength={1000}
                placeholder="Decision reason (recommended; required for clear rejection audits)"
                className="mt-3 min-h-20 w-full rounded-lg border border-line bg-raised px-3 py-2 text-sm text-ink outline-none transition placeholder:text-slate-muted focus:border-teal focus:ring-2 focus:ring-teal/20"
              />
            ) : null}
          </li>
        ))}
      </ul>
    </div>
  );
}
