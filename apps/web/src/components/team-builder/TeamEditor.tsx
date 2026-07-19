"use client";

import { useMemo, useState } from "react";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Input, Label, Select, Textarea } from "@/components/ui/Field";
import { publishTeam, saveTeamDraft } from "@/lib/api/admin";
import {
  ALLOWED_MODELS,
  type AgentSummary,
  type ModelId,
  type TeamConfig,
  type TeamDraftInput,
  type TeamMode,
} from "@/lib/api/types";
import { useAgentOsToken } from "@/lib/auth/token";
import { formatRelative } from "@/lib/utils";

export function TeamEditor({
  initial,
  agents,
}: {
  initial: TeamConfig;
  agents: AgentSummary[];
}) {
  const { getAccessToken } = useAgentOsToken();
  const [form, setForm] = useState<TeamDraftInput>({
    name: initial.name,
    description: initial.description,
    instructions: initial.instructions,
    mode: initial.mode,
    model: initial.model,
    temperature: initial.temperature,
    memberConfigIds: initial.members.map((member) => member.agentConfigId),
  });
  const [status, setStatus] = useState(initial.status);
  const [draftVersion, setDraftVersion] = useState(initial.draftVersion);
  const [publishedVersion, setPublishedVersion] = useState(initial.publishedVersion);
  const [busy, setBusy] = useState<"save" | "publish" | null>(null);
  const [banner, setBanner] = useState<string | null>(null);

  const agentMap = useMemo(
    () => new Map(agents.map((agent) => [agent.id, agent])),
    [agents],
  );

  function update<K extends keyof TeamDraftInput>(
    key: K,
    value: TeamDraftInput[K],
  ) {
    setForm((previous) => ({ ...previous, [key]: value }));
  }

  function toggleMember(agentId: string) {
    setForm((previous) => ({
      ...previous,
      memberConfigIds: previous.memberConfigIds.includes(agentId)
        ? previous.memberConfigIds.filter((id) => id !== agentId)
        : [...previous.memberConfigIds, agentId],
    }));
  }

  function moveMember(index: number, direction: -1 | 1) {
    setForm((previous) => {
      const next = [...previous.memberConfigIds];
      const target = index + direction;
      if (target < 0 || target >= next.length) return previous;
      [next[index], next[target]] = [next[target], next[index]];
      return { ...previous, memberConfigIds: next };
    });
  }

  async function save() {
    if (!form.name.trim() || !form.instructions.trim()) {
      setBanner("Name and instructions are required");
      return null;
    }
    setBusy("save");
    setBanner(null);
    try {
      const saved = await saveTeamDraft(
        await getAccessToken(),
        initial.id,
        form,
      );
      setStatus(saved.status);
      setDraftVersion(saved.draftVersion);
      setBanner("Draft saved");
      return saved;
    } catch (err) {
      setBanner(err instanceof Error ? err.message : "Save failed");
      return null;
    } finally {
      setBusy(null);
    }
  }

  async function publish() {
    if (form.memberConfigIds.length < 2) {
      setBanner("Add at least two agents before publishing");
      return;
    }
    setBusy("publish");
    setBanner(null);
    try {
      await saveTeamDraft(await getAccessToken(), initial.id, form);
      const published = await publishTeam(await getAccessToken(), initial.id);
      setStatus(published.status);
      setDraftVersion(published.draftVersion);
      setPublishedVersion(published.publishedVersion);
      setBanner(`Published v${published.publishedVersion}`);
    } catch (err) {
      setBanner(err instanceof Error ? err.message : "Publish failed");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.14em] text-teal">
            Team editor
          </p>
          <h1 className="font-display text-3xl font-semibold tracking-tight">
            {form.name || "Untitled team"}
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
        <div className="rounded-lg border border-teal/30 bg-teal/10 px-3 py-2 text-sm text-ink-soft">
          {banner}
        </div>
      ) : null}

      <div className="space-y-5">
          <section className="surface-panel rounded-2xl p-5">
            <h2 className="font-display text-lg font-semibold">Identity</h2>
            <div className="mt-4 grid gap-4 md:grid-cols-2">
              <div>
                <Label htmlFor="team-name">Name</Label>
                <Input
                  id="team-name"
                  value={form.name}
                  onChange={(event) => update("name", event.target.value)}
                />
              </div>
              <div>
                <Label htmlFor="team-slug" hint="immutable">
                  Slug
                </Label>
                <Input id="team-slug" value={initial.slug} disabled />
              </div>
              <div className="md:col-span-2">
                <Label htmlFor="team-description">Description</Label>
                <Input
                  id="team-description"
                  value={form.description}
                  onChange={(event) => update("description", event.target.value)}
                />
              </div>
            </div>
          </section>

          <section className="surface-panel rounded-2xl p-5">
            <h2 className="font-display text-lg font-semibold">Coordination</h2>
            <div className="mt-4 grid gap-4 md:grid-cols-2">
              <div>
                <Label htmlFor="team-mode">Mode</Label>
                <Select
                  id="team-mode"
                  value={form.mode}
                  onChange={(event) => update("mode", event.target.value as TeamMode)}
                >
                  <option value="route">Route</option>
                  <option value="coordinate">Coordinate</option>
                </Select>
              </div>
              <div>
                <Label htmlFor="team-model">Leader model</Label>
                <Select
                  id="team-model"
                  value={form.model}
                  onChange={(event) => update("model", event.target.value as ModelId)}
                >
                  {ALLOWED_MODELS.map((model) => (
                    <option key={model.id} value={model.id}>{model.label}</option>
                  ))}
                </Select>
              </div>
              <div className="md:col-span-2">
                <Label htmlFor="team-temperature" hint={form.temperature.toFixed(2)}>
                  Leader temperature
                </Label>
                <input
                  id="team-temperature"
                  type="range"
                  min={0}
                  max={1.5}
                  step={0.05}
                  value={form.temperature}
                  onChange={(event) =>
                    update("temperature", Number.parseFloat(event.target.value))
                  }
                  className="w-full accent-teal"
                />
              </div>
              <div className="md:col-span-2">
                <Label htmlFor="team-instructions">Team instructions</Label>
                <Textarea
                  id="team-instructions"
                  value={form.instructions}
                  onChange={(event) => update("instructions", event.target.value)}
                  className="min-h-40 font-mono text-[13px]"
                />
              </div>
            </div>
          </section>

          <section className="surface-panel rounded-2xl p-5">
            <div className="flex items-center justify-between gap-3">
              <h2 className="font-display text-lg font-semibold">Members</h2>
              <Badge tone={form.memberConfigIds.length >= 2 ? "success" : "warning"}>
                {form.memberConfigIds.length} selected
              </Badge>
            </div>
            <p className="mt-2 text-sm text-slate-muted">
              Order controls routing priority. Publishing pins each selected agent&apos;s
              current published version.
            </p>
            <ul className="mt-4 space-y-2">
              {form.memberConfigIds.map((id, index) => {
                const agent = agentMap.get(id);
                if (!agent) return null;
                return (
                  <li key={id} className="flex items-center gap-3 rounded-xl border border-line bg-raised/70 p-3">
                    <span className="w-6 text-center text-xs font-semibold text-slate-muted">
                      {index + 1}
                    </span>
                    <div className="min-w-0 flex-1">
                      <p className="font-medium">{agent.name}</p>
                      <p className="text-xs text-slate-muted">/{agent.slug}</p>
                    </div>
                    <Badge tone={agent.status === "published" ? "success" : "warning"}>
                      {agent.status}
                    </Badge>
                    <Button variant="secondary" onClick={() => moveMember(index, -1)} disabled={index === 0}>↑</Button>
                    <Button variant="secondary" onClick={() => moveMember(index, 1)} disabled={index === form.memberConfigIds.length - 1}>↓</Button>
                    <Button variant="secondary" onClick={() => toggleMember(id)}>Remove</Button>
                  </li>
                );
              })}
            </ul>
            <div className="mt-5 border-t border-line pt-4">
              <p className="mb-2 text-xs font-semibold uppercase tracking-[0.1em] text-slate-muted">
                Available agents
              </p>
              <div className="grid gap-2 sm:grid-cols-2">
                {agents
                  .filter((agent) => !form.memberConfigIds.includes(agent.id))
                  .map((agent) => (
                    <button
                      key={agent.id}
                      type="button"
                      onClick={() => toggleMember(agent.id)}
                      className="rounded-lg border border-line bg-raised/60 p-3 text-left transition hover:border-teal/50"
                    >
                      <span className="font-medium">{agent.name}</span>
                      <span className="ml-2 text-xs text-slate-muted">Add</span>
                    </button>
                  ))}
              </div>
            </div>
          </section>
      </div>
    </div>
  );
}
