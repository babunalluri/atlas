"use client";

import { useMemo, useState } from "react";
import { useTranslations } from "next-intl";
import { useRouter } from "@/i18n/navigation";

import { BackLink } from "@/components/ui/BackLink";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { EditorActions } from "@/components/ui/EditorActions";
import { Input, Label, Select, Textarea } from "@/components/ui/Field";
import { ModelSelect } from "@/components/ui/ModelSelect";
import { PublishIcon, SaveIcon, TrashIcon } from "@/components/ui/icons";
import { ToolAttachmentSection } from "@/components/tools/ToolAttachmentSection";
import {
  deleteTeam,
  publishTeam,
  saveTeamDraft,
  type CredentialSummary,
} from "@/lib/api/admin";
import {
  type AgentSummary,
  type TeamConfig,
  type TeamDraftInput,
  type TeamMode,
  type ToolDefinition,
} from "@/lib/api/types";
import { useAgentOsToken } from "@/lib/auth/token";
import { formatRelative } from "@/lib/utils";

function applyTeamToForm(team: TeamConfig): TeamDraftInput {
  return {
    name: team.name,
    description: team.description,
    instructions: team.instructions,
    mode: team.mode,
    model: team.model,
    temperature: team.temperature,
    memberConfigIds: team.members.map((member) => member.agentConfigId),
    tools: team.tools,
  };
}

export function TeamEditor({
  initial,
  agents,
  toolDefinitions = [],
  credentials = [],
}: {
  initial: TeamConfig;
  agents: AgentSummary[];
  toolDefinitions?: ToolDefinition[];
  credentials?: CredentialSummary[];
}) {
  const { getAccessToken } = useAgentOsToken();
  const router = useRouter();
  const tCommon = useTranslations("common");
  const [form, setForm] = useState<TeamDraftInput>(() => applyTeamToForm(initial));
  const [status, setStatus] = useState(initial.status);
  const [draftVersion, setDraftVersion] = useState(initial.draftVersion);
  const [publishedVersion, setPublishedVersion] = useState(initial.publishedVersion);
  const [busy, setBusy] = useState<"save" | "publish" | "delete" | null>(null);
  const [banner, setBanner] = useState<string | null>(null);

  const agentMap = useMemo(
    () => new Map(agents.map((agent) => [agent.id, agent])),
    [agents],
  );
  const available = agents.filter(
    (agent) => !form.memberConfigIds.includes(agent.id),
  );

  const credentialMap = useMemo(
    () => new Map(credentials.map((credential) => [credential.id, credential.name])),
    [credentials],
  );

  const credentialNames = useMemo(
    () => Object.fromEntries(credentialMap.entries()),
    [credentialMap],
  );

  function applyTeam(team: TeamConfig) {
    setForm(applyTeamToForm(team));
    setStatus(team.status);
    setDraftVersion(team.draftVersion);
    setPublishedVersion(team.publishedVersion);
  }

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
      applyTeam(saved);
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
    setBusy("publish");
    setBanner(null);
    try {
      await saveTeamDraft(await getAccessToken(), initial.id, form);
      const published = await publishTeam(await getAccessToken(), initial.id);
      applyTeam(published);
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
        `Delete “${form.name || "this team"}”? This cannot be undone.`,
      )
    ) {
      return;
    }
    setBusy("delete");
    setBanner(null);
    try {
      await deleteTeam(await getAccessToken(), initial.id);
      router.push("/admin/teams");
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
            <BackLink href="/admin/teams" label="Back to teams" />
            <h1 className="min-w-0 truncate py-0.5 font-display text-2xl font-semibold leading-snug tracking-tight">
              {form.name || "Untitled team"}
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
            {busy === "save" ? tCommon("saving") : tCommon("save")}
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
            <Label htmlFor="team-name">Name</Label>
            <Input
              id="team-name"
              value={form.name}
              onChange={(event) => update("name", event.target.value)}
            />
          </div>
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
          <div className="md:col-span-3">
            <Label htmlFor="team-description">Description</Label>
            <Input
              id="team-description"
              value={form.description}
              onChange={(event) => update("description", event.target.value)}
              placeholder="Optional"
            />
          </div>
          <ModelSelect
            id="team-model"
            label="Leader model"
            value={form.model}
            onChange={(model) => update("model", model)}
            credentials={credentials}
          />
          <div>
            <Label htmlFor="team-temperature" hint={form.temperature.toFixed(2)}>
              Temperature
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
              className="mt-2 w-full accent-teal"
            />
          </div>
          <div className="md:col-span-3">
            <Label htmlFor="team-instructions">Instructions</Label>
            <Textarea
              id="team-instructions"
              value={form.instructions}
              onChange={(event) => update("instructions", event.target.value)}
              rows={4}
              className="min-h-0 font-mono text-[13px]"
            />
          </div>
        </div>
      </section>

      <ToolAttachmentSection
        tools={form.tools}
        onChange={(tools) => update("tools", tools)}
        toolDefinitions={toolDefinitions}
        credentialNames={credentialNames}
        description="Attached to the team leader (in addition to tools on member agents)."
      />

      <section className="rounded-xl border border-line bg-raised/40 p-4">
        <div className="mb-3 flex items-center justify-between gap-2">
          <h2 className="text-sm font-semibold">Members</h2>
          <Badge tone="neutral">
            {form.memberConfigIds.length} selected
          </Badge>
        </div>
        <p className="mb-3 text-xs text-slate-muted">
          Optional specialists for routing. The leader can use team tools
          without members. Order is routing priority.
        </p>

        {form.memberConfigIds.length === 0 ? (
          <p className="mb-3 rounded-md border border-dashed border-line px-3 py-4 text-center text-sm text-slate-muted">
            No members — leader-only team (tools on the leader still work).
          </p>
        ) : (
          <ul className="mb-3 space-y-1.5">
            {form.memberConfigIds.map((id, index) => {
              const agent = agentMap.get(id);
              if (!agent) return null;
              return (
                <li
                  key={id}
                  className="flex items-center gap-2 rounded-md border border-line bg-canvas/40 px-2.5 py-2"
                >
                  <span className="w-5 text-center text-xs text-slate-muted">
                    {index + 1}
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium">{agent.name}</p>
                  </div>
                  <Badge
                    tone={agent.status === "published" ? "success" : "warning"}
                  >
                    {agent.status}
                  </Badge>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => moveMember(index, -1)}
                    disabled={index === 0}
                  >
                    ↑
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => moveMember(index, 1)}
                    disabled={index === form.memberConfigIds.length - 1}
                  >
                    ↓
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => toggleMember(id)}
                  >
                    Remove
                  </Button>
                </li>
              );
            })}
          </ul>
        )}

        <div className="border-t border-line pt-3">
          <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-muted">
            Add agents
          </p>
          {available.length === 0 ? (
            <p className="text-sm text-slate-muted">
              All agents are already on this team.
            </p>
          ) : (
            <div className="flex flex-wrap gap-1.5">
              {available.map((agent) => (
                <button
                  key={agent.id}
                  type="button"
                  onClick={() => toggleMember(agent.id)}
                  className="rounded-md border border-line bg-raised px-2.5 py-1.5 text-left text-sm hover:border-teal/50"
                >
                  <span className="font-medium">{agent.name}</span>
                  <span className="ml-1.5 text-xs text-slate-muted">+ Add</span>
                </button>
              ))}
            </div>
          )}
        </div>
      </section>
    </div>
  );
}
