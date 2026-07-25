"use client";

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";

import { BackLink } from "@/components/ui/BackLink";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { EditorActions } from "@/components/ui/EditorActions";
import { Input, Label, Select } from "@/components/ui/Field";
import { PublishIcon, SaveIcon, TrashIcon } from "@/components/ui/icons";
import {
  deleteWorkflow,
  publishWorkflow,
  saveWorkflowDraft,
} from "@/lib/api/admin";
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

function applyWorkflowToForm(workflow: WorkflowConfig): WorkflowDraftInput {
  return {
    name: workflow.name,
    description: workflow.description,
    mode: workflow.mode,
    steps: workflow.steps,
  };
}

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
  const router = useRouter();
  const [form, setForm] = useState<WorkflowDraftInput>(() =>
    applyWorkflowToForm(initial),
  );
  const [status, setStatus] = useState(initial.status);
  const [draftVersion, setDraftVersion] = useState(initial.draftVersion);
  const [publishedVersion, setPublishedVersion] = useState(initial.publishedVersion);
  const [busy, setBusy] = useState<"save" | "publish" | "delete" | null>(null);
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

  function applyWorkflow(workflow: WorkflowConfig) {
    setForm(applyWorkflowToForm(workflow));
    setStatus(workflow.status);
    setDraftVersion(workflow.draftVersion);
    setPublishedVersion(workflow.publishedVersion);
  }

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
        },
      ],
    }));
  }

  function updateStep(index: number, patch: Partial<WorkflowStep>) {
    setForm((previous) => ({
      ...previous,
      steps: previous.steps.map((step, position) =>
        position === index ? { ...step, ...patch } : step,
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
      setBanner("Name and at least one step are required");
      return;
    }
    setBusy("save");
    setBanner(null);
    try {
      const saved = await saveWorkflowDraft(
        await getAccessToken(),
        initial.id,
        form,
      );
      applyWorkflow(saved);
      setBanner("Draft saved");
    } catch (err) {
      setBanner(err instanceof Error ? err.message : "Save failed");
    } finally {
      setBusy(null);
    }
  }

  async function publish() {
    if (form.steps.length === 0) {
      setBanner("Add at least one step before publishing");
      return;
    }
    setBusy("publish");
    setBanner(null);
    try {
      await saveWorkflowDraft(await getAccessToken(), initial.id, form);
      const published = await publishWorkflow(await getAccessToken(), initial.id);
      applyWorkflow(published);
      setBanner(`Published v${published.publishedVersion}`);
    } catch (err) {
      setBanner(err instanceof Error ? err.message : "Publish failed");
    } finally {
      setBusy(null);
    }
  }

  async function remove() {
    if (
      !window.confirm(
        `Delete “${form.name || "this workflow"}”? This cannot be undone.`,
      )
    ) {
      return;
    }
    setBusy("delete");
    setBanner(null);
    try {
      await deleteWorkflow(await getAccessToken(), initial.id);
      router.push("/admin/workflows");
    } catch (err) {
      setBanner(err instanceof Error ? err.message : "Delete failed");
      setBusy(null);
    }
  }

  return (
    <div className="space-y-3">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <div className="flex min-w-0 items-center gap-1.5">
            <BackLink href="/admin/workflows" label="Back to workflows" />
            <h1 className="truncate font-display text-2xl font-semibold tracking-tight">
              {form.name || "Untitled workflow"}
            </h1>
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-slate-muted">
            <Badge tone={status === "published" ? "success" : "warning"}>
              {status}
            </Badge>
            <span>draft v{draftVersion}</span>
            {publishedVersion ? <span>live v{publishedVersion}</span> : null}
            <span>/{initial.slug}</span>
            <span>{formatRelative(initial.updatedAt)}</span>
          </div>
        </div>
        <EditorActions>
          <Button
            variant="danger"
            size="sm"
            onClick={() => void remove()}
            disabled={busy !== null}
          >
            <TrashIcon />
            {busy === "delete" ? "Deleting…" : "Delete"}
          </Button>
          <Button
            variant="secondary"
            size="sm"
            onClick={save}
            disabled={busy !== null}
          >
            <SaveIcon />
            {busy === "save" ? "Saving…" : "Save"}
          </Button>
          <Button
            variant="accent"
            size="sm"
            onClick={publish}
            disabled={busy !== null}
          >
            <PublishIcon />
            {busy === "publish" ? "Publishing…" : "Publish"}
          </Button>
        </EditorActions>
      </header>

      {banner ? (
        <p className="rounded-md border border-teal/30 bg-teal/10 px-3 py-1.5 text-sm">
          {banner}
        </p>
      ) : null}

      <section className="rounded-xl border border-line bg-raised/40 p-4">
        <div className="grid gap-3 md:grid-cols-3">
          <div className="md:col-span-2">
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
            <Label htmlFor="workflow-mode">Mode</Label>
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
          <div className="md:col-span-3">
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
              placeholder="Optional"
            />
          </div>
        </div>
      </section>

      <section className="rounded-xl border border-line bg-raised/40 p-4">
        <div className="mb-2 flex items-center justify-between gap-2">
          <h2 className="text-sm font-semibold">Steps</h2>
          <Badge tone={form.steps.length > 0 ? "success" : "warning"}>
            {form.steps.length} steps
          </Badge>
        </div>

        {form.steps.length === 0 ? (
          <p className="mb-3 rounded-md border border-dashed border-line px-3 py-4 text-center text-sm text-slate-muted">
            No steps yet — add a published agent or team below.
          </p>
        ) : (
          <ol className="mb-3 space-y-1.5">
            {form.steps.map((step, index) => (
              <li
                key={`${step.targetConfigId}-${index}`}
                className="flex flex-wrap items-center gap-2 rounded-md border border-line bg-canvas/40 px-2.5 py-2"
              >
                <span className="w-5 shrink-0 text-center text-xs text-slate-muted">
                  {index + 1}
                </span>
                <Input
                  aria-label={`Step ${index + 1} name`}
                  value={step.name}
                  onChange={(event) =>
                    updateStep(index, { name: event.target.value })
                  }
                  className="min-w-0 max-w-xs flex-1 py-1.5"
                />
                <span className="hidden min-w-0 truncate text-xs text-slate-muted sm:inline">
                  {step.targetName || step.targetSlug}
                </span>
                <Badge>{step.targetType}</Badge>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => move(index, -1)}
                  disabled={index === 0}
                >
                  ↑
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => move(index, 1)}
                  disabled={index === form.steps.length - 1}
                >
                  ↓
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() =>
                    setForm((previous) => ({
                      ...previous,
                      steps: previous.steps.filter(
                        (_, position) => position !== index,
                      ),
                    }))
                  }
                >
                  Remove
                </Button>
              </li>
            ))}
          </ol>
        )}

        <div className="border-t border-line pt-2">
          <p className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-slate-muted">
            Add step
          </p>
          <div className="flex flex-wrap gap-1">
            {targets.map((target) => (
              <button
                key={`${target.type}:${target.id}`}
                type="button"
                onClick={() => addStep(target)}
                className="rounded-md border border-line bg-raised px-2 py-1 text-left text-xs hover:border-teal/50"
              >
                <span className="font-medium">{target.name}</span>
                <span className="ml-1 capitalize text-slate-muted">
                  {target.type}
                </span>
              </button>
            ))}
            {targets.length === 0 ? (
              <p className="text-sm text-slate-muted">
                Publish an agent or team first.
              </p>
            ) : null}
          </div>
        </div>
      </section>
    </div>
  );
}
