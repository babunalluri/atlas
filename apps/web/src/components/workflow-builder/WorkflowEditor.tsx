"use client";

import { useMemo, useState } from "react";

import { WorkflowAccessPanel } from "@/components/workflow-builder/WorkflowAccessPanel";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Input, Label, Select } from "@/components/ui/Field";
import { publishWorkflow, saveWorkflowDraft } from "@/lib/api/admin";
import type {
  AgentSummary,
  TeamSummary,
  WorkflowConfig,
  WorkflowDraftInput,
  WorkflowMode,
  WorkflowStep,
} from "@/lib/api/types";
import { useAgentOsToken } from "@/lib/auth/token";
import { formatRelative } from "@/lib/utils";

export function WorkflowEditor({
  initial,
  agents,
  teams,
}: {
  initial: WorkflowConfig;
  agents: AgentSummary[];
  teams: TeamSummary[];
}) {
  const { getAccessToken } = useAgentOsToken();
  const [form, setForm] = useState<WorkflowDraftInput>({
    name: initial.name,
    description: initial.description,
    mode: initial.mode,
    steps: initial.steps,
  });
  const [status, setStatus] = useState(initial.status);
  const [draftVersion, setDraftVersion] = useState(initial.draftVersion);
  const [publishedVersion, setPublishedVersion] = useState(initial.publishedVersion);
  const [busy, setBusy] = useState<"save" | "publish" | null>(null);
  const [banner, setBanner] = useState<string | null>(null);

  const targets = useMemo(
    () => [
      ...agents.map((agent) => ({
        id: agent.id,
        name: agent.name,
        slug: agent.slug,
        type: "agent" as const,
        status: agent.status,
      })),
      ...teams.map((team) => ({
        id: team.id,
        name: team.name,
        slug: team.slug,
        type: "team" as const,
        status: team.status,
      })),
    ],
    [agents, teams],
  );

  function addStep(target: (typeof targets)[number]) {
    setForm((previous) => ({
      ...previous,
      steps: [
        ...previous.steps,
        {
          name: target.name,
          targetType: target.type,
          targetConfigId: target.id,
          targetName: target.name,
          targetSlug: target.slug,
          targetStatus: target.status,
          conditionExpression: null,
        },
      ],
    }));
  }

  function updateStep(index: number, values: Partial<WorkflowStep>) {
    setForm((previous) => ({
      ...previous,
      steps: previous.steps.map((step, position) =>
        position === index ? { ...step, ...values } : step,
      ),
    }));
  }

  function move(index: number, direction: -1 | 1) {
    setForm((previous) => {
      const next = [...previous.steps];
      const target = index + direction;
      if (target < 0 || target >= next.length) return previous;
      [next[index], next[target]] = [next[target], next[index]];
      return { ...previous, steps: next };
    });
  }

  async function save() {
    if (!form.name.trim() || form.steps.length === 0) {
      setBanner("A name and at least one step are required");
      return null;
    }
    setBusy("save");
    setBanner(null);
    try {
      const saved = await saveWorkflowDraft(
        await getAccessToken(),
        initial.id,
        form,
      );
      setStatus(saved.status);
      setDraftVersion(saved.draftVersion);
      setBanner("Draft saved");
      return saved;
    } catch (reason) {
      setBanner(reason instanceof Error ? reason.message : "Save failed");
      return null;
    } finally {
      setBusy(null);
    }
  }

  async function publish() {
    setBusy("publish");
    setBanner(null);
    try {
      const saved = await saveWorkflowDraft(
        await getAccessToken(),
        initial.id,
        form,
      );
      if (saved.steps.length === 0) throw new Error("Add at least one step");
      const published = await publishWorkflow(await getAccessToken(), initial.id);
      setStatus(published.status);
      setDraftVersion(published.draftVersion);
      setPublishedVersion(published.publishedVersion);
      setBanner(`Published v${published.publishedVersion}`);
    } catch (reason) {
      setBanner(reason instanceof Error ? reason.message : "Publish failed");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.14em] text-teal">
            Atlas workflow editor
          </p>
          <h1 className="font-display text-3xl font-semibold tracking-tight">
            {form.name || "Untitled workflow"}
          </h1>
          <div className="mt-2 flex flex-wrap items-center gap-2 text-sm text-slate-muted">
            <Badge tone={status === "published" ? "success" : "warning"}>{status}</Badge>
            <span>draft v{draftVersion}</span>
            {publishedVersion ? <span>live v{publishedVersion}</span> : null}
            <span>updated {formatRelative(initial.updatedAt)}</span>
          </div>
        </div>
        <div className="flex gap-2">
          <Button variant="secondary" onClick={save} disabled={busy !== null}>
            {busy === "save" ? "Saving…" : "Save draft"}
          </Button>
          <Button variant="accent" onClick={publish} disabled={busy !== null}>
            {busy === "publish" ? "Publishing…" : "Publish"}
          </Button>
        </div>
      </div>

      {banner ? (
        <div className="rounded-lg border border-teal/30 bg-teal/10 px-3 py-2 text-sm">
          {banner}
        </div>
      ) : null}

      <div className="space-y-5">
          <section className="surface-panel rounded-2xl p-5">
            <h2 className="font-display text-lg font-semibold">Definition</h2>
            <div className="mt-4 grid gap-4 md:grid-cols-2">
              <div>
                <Label htmlFor="workflow-name">Name</Label>
                <Input
                  id="workflow-name"
                  value={form.name}
                  onChange={(event) =>
                    setForm((previous) => ({ ...previous, name: event.target.value }))
                  }
                />
              </div>
              <div>
                <Label htmlFor="workflow-slug" hint="immutable">Slug</Label>
                <Input id="workflow-slug" value={initial.slug} disabled />
              </div>
              <div className="md:col-span-2">
                <Label htmlFor="workflow-description">Description</Label>
                <Input
                  id="workflow-description"
                  value={form.description}
                  onChange={(event) =>
                    setForm((previous) => ({
                      ...previous,
                      description: event.target.value,
                    }))
                  }
                />
              </div>
              <div>
                <Label htmlFor="workflow-mode">Execution mode</Label>
                <Select
                  id="workflow-mode"
                  value={form.mode}
                  onChange={(event) =>
                    setForm((previous) => ({
                      ...previous,
                      mode: event.target.value as WorkflowMode,
                    }))
                  }
                >
                  <option value="sequential">Sequential</option>
                  <option value="parallel">Parallel</option>
                </Select>
              </div>
            </div>
          </section>

          <section className="surface-panel rounded-2xl p-5">
            <div className="flex items-center justify-between">
              <h2 className="font-display text-lg font-semibold">Ordered steps</h2>
              <Badge tone={form.steps.length ? "success" : "warning"}>
                {form.steps.length} steps
              </Badge>
            </div>
            <p className="mt-2 text-sm text-slate-muted">
              Publishing pins each target&apos;s current published version. CEL
              conditions are reserved in the schema and currently disabled.
            </p>
            <ol className="mt-4 space-y-2">
              {form.steps.map((step, index) => (
                <li
                  key={`${step.targetType}:${step.targetConfigId}:${index}`}
                  className="rounded-xl border border-line bg-raised/70 p-3"
                >
                  <div className="flex items-center gap-3">
                    <span className="w-6 text-center text-xs font-semibold text-slate-muted">
                      {index + 1}
                    </span>
                    <Input
                      aria-label={`Step ${index + 1} name`}
                      value={step.name}
                      onChange={(event) => updateStep(index, { name: event.target.value })}
                      className="min-w-0 flex-1"
                    />
                    <Badge>{step.targetType}</Badge>
                    <span className="hidden text-xs text-slate-muted sm:inline">
                      {step.targetName ?? step.targetSlug}
                    </span>
                    <Button variant="secondary" onClick={() => move(index, -1)} disabled={index === 0}>↑</Button>
                    <Button variant="secondary" onClick={() => move(index, 1)} disabled={index === form.steps.length - 1}>↓</Button>
                    <Button
                      variant="secondary"
                      onClick={() =>
                        setForm((previous) => ({
                          ...previous,
                          steps: previous.steps.filter((_, position) => position !== index),
                        }))
                      }
                    >
                      Remove
                    </Button>
                  </div>
                </li>
              ))}
            </ol>
            <div className="mt-5 border-t border-line pt-4">
              <p className="mb-2 text-xs font-semibold uppercase tracking-[0.1em] text-slate-muted">
                Add a tenant component
              </p>
              <div className="grid gap-2 sm:grid-cols-2">
                {targets.map((target) => (
                  <button
                    key={`${target.type}:${target.id}`}
                    type="button"
                    onClick={() => addStep(target)}
                    className="rounded-lg border border-line bg-raised/60 p-3 text-left transition hover:border-teal/50"
                  >
                    <span className="font-medium">{target.name}</span>
                    <span className="ml-2 text-xs capitalize text-slate-muted">
                      {target.type} · {target.status}
                    </span>
                  </button>
                ))}
              </div>
            </div>
          </section>
      </div>
      <WorkflowAccessPanel workflowId={initial.id} />
    </div>
  );
}
