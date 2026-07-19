"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Input, Label } from "@/components/ui/Field";
import { createWorkflow } from "@/lib/api/admin";
import type { WorkflowSummary } from "@/lib/api/types";
import { useAgentOsToken } from "@/lib/auth/token";
import { formatRelative } from "@/lib/utils";
import { slugifyName } from "@/lib/validation/agent-form";

export function WorkflowList({ workflows }: { workflows: WorkflowSummary[] }) {
  const router = useRouter();
  const { getAccessToken } = useAgentOsToken();
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function create() {
    setBusy(true);
    setError(null);
    try {
      const label = name.trim() || "Untitled workflow";
      const workflow = await createWorkflow(await getAccessToken(), {
        name: label,
        slug: slugifyName(name || `untitled-workflow-${Date.now()}`),
      });
      router.push(`/admin/workflows/${workflow.id}`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Creation failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-8">
      <section className="surface-panel relative overflow-hidden rounded-2xl p-6 md:p-8">
        <div className="pointer-events-none absolute inset-0 grid-noise opacity-60" />
        <div className="relative grid gap-6 md:grid-cols-[1.4fr_1fr] md:items-end">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.16em] text-teal">
              Atlas workflows
            </p>
            <h1 className="mt-2 font-display text-4xl font-semibold tracking-tight md:text-5xl">
              Turn specialists into repeatable systems.
            </h1>
            <p className="mt-3 max-w-xl text-sm leading-relaxed text-slate-muted">
              Compose tenant agents and teams into versioned sequential or parallel
              runs, then publish pinned releases for customer use.
            </p>
          </div>
          <div className="rounded-xl border border-line bg-raised/90 p-4">
            <Label htmlFor="new-workflow">New workflow</Label>
            <div className="flex gap-2">
              <Input
                id="new-workflow"
                placeholder="e.g. Account onboarding"
                value={name}
                onChange={(event) => setName(event.target.value)}
              />
              <Button variant="accent" onClick={create} disabled={busy}>
                {busy ? "Creating…" : "Create"}
              </Button>
            </div>
            {error ? <p className="mt-2 text-xs text-rose">{error}</p> : null}
          </div>
        </div>
      </section>

      <section className="table-shell rounded-xl">
        <div className="grid grid-cols-[1.5fr_0.7fr_0.7fr_0.6fr] gap-3 border-b border-line px-4 py-2.5">
          <span className="th-label">Workflow</span>
          <span className="th-label">Mode</span>
          <span className="th-label">Status</span>
          <span className="th-label text-right">Updated</span>
        </div>
        <ul>
          {workflows.map((workflow) => (
            <li key={workflow.id} className="border-b border-line/60 last:border-0">
              <Link
                href={`/admin/workflows/${workflow.id}`}
                className="grid grid-cols-[1.5fr_0.7fr_0.7fr_0.6fr] items-center gap-3 px-4 py-2.5 transition hover:bg-mist/70"
              >
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium">{workflow.name}</p>
                  <p className="mono-cell truncate text-slate-muted">
                    /{workflow.slug} · {workflow.stepCount} steps
                  </p>
                </div>
                <p className="text-sm capitalize text-ink-soft">{workflow.mode}</p>
                <Badge dot tone={workflow.status === "published" ? "success" : "warning"}>
                  {workflow.status}
                </Badge>
                <p className="mono-cell text-right text-slate-muted">
                  {formatRelative(workflow.updatedAt)}
                </p>
              </Link>
            </li>
          ))}
          {workflows.length === 0 ? (
            <li className="px-4 py-10 text-center text-sm text-slate-muted">
              No workflows yet — create the first Atlas flow above.
            </li>
          ) : null}
        </ul>
      </section>
    </div>
  );
}
