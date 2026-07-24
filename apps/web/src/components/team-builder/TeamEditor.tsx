"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";

import { BackLink } from "@/components/ui/BackLink";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { EditorActions } from "@/components/ui/EditorActions";
import { Input, Label, Select, Textarea } from "@/components/ui/Field";
import { PublishIcon, SaveIcon, TrashIcon } from "@/components/ui/icons";
import {
  VersionHistoryPanel,
  type VersionHistoryItem,
} from "@/components/ui/VersionHistoryPanel";
import {
  deleteTeam,
  getTeamVersion,
  listTeamVersions,
  publishTeam,
  restoreTeamVersion,
  saveTeamDraft,
} from "@/lib/api/admin";
import {
  ALLOWED_MODELS,
  type AgentSummary,
  type ModelId,
  type TeamConfig,
  type TeamDraftInput,
  type TeamMode,
  type TeamVersionDetail,
  type TeamVersionSummary,
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
  };
}

export function TeamEditor({
  initial,
  agents,
}: {
  initial: TeamConfig;
  agents: AgentSummary[];
}) {
  const { getAccessToken } = useAgentOsToken();
  const router = useRouter();
  const [form, setForm] = useState<TeamDraftInput>(() => applyTeamToForm(initial));
  const [status, setStatus] = useState(initial.status);
  const [draftVersion, setDraftVersion] = useState(initial.draftVersion);
  const [publishedVersion, setPublishedVersion] = useState(initial.publishedVersion);
  const [busy, setBusy] = useState<
    "save" | "publish" | "delete" | "versions" | "restore" | null
  >(null);
  const [banner, setBanner] = useState<string | null>(null);
  const [versions, setVersions] = useState<TeamVersionSummary[]>([]);
  const [viewing, setViewing] = useState<TeamVersionDetail | null>(null);

  const agentMap = useMemo(
    () => new Map(agents.map((agent) => [agent.id, agent])),
    [agents],
  );
  const available = agents.filter(
    (agent) => !form.memberConfigIds.includes(agent.id),
  );

  function applyTeam(team: TeamConfig) {
    setForm(applyTeamToForm(team));
    setStatus(team.status);
    setDraftVersion(team.draftVersion);
    setPublishedVersion(team.publishedVersion);
  }

  async function refreshVersions() {
    setBusy("versions");
    try {
      const rows = await listTeamVersions(await getAccessToken(), initial.id);
      setVersions(rows);
    } catch (err) {
      setBanner(err instanceof Error ? err.message : "Failed to load versions");
    } finally {
      setBusy((current) => (current === "versions" ? null : current));
    }
  }

  useEffect(() => {
    void refreshVersions();
    // Load once on mount for this team.
    // eslint-disable-next-line react-hooks/exhaustive-deps -- intentional mount-only
  }, [initial.id]);

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
      void refreshVersions();
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
      applyTeam(published);
      setBanner(`Published v${published.publishedVersion}`);
      void refreshVersions();
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

  async function viewVersion(version: VersionHistoryItem) {
    setBusy("versions");
    setBanner(null);
    try {
      const detail = await getTeamVersion(
        await getAccessToken(),
        initial.id,
        version.id,
      );
      setViewing(detail);
    } catch (err) {
      setBanner(err instanceof Error ? err.message : "Failed to load version");
    } finally {
      setBusy(null);
    }
  }

  async function restoreLive(version: VersionHistoryItem) {
    if (version.isLive) {
      setBanner(`v${version.version} is already live`);
      return;
    }
    if (
      !window.confirm(
        `Make v${version.version} the live published version? Current live stays available in history.`,
      )
    ) {
      return;
    }
    setBusy("restore");
    setBanner(null);
    try {
      const restored = await restoreTeamVersion(
        await getAccessToken(),
        initial.id,
        version.id,
      );
      applyTeam(restored);
      setViewing(null);
      setBanner(`Restored live to v${restored.publishedVersion}`);
      void refreshVersions();
    } catch (err) {
      setBanner(err instanceof Error ? err.message : "Restore failed");
    } finally {
      setBusy(null);
    }
  }

  async function restoreDraft(version: VersionHistoryItem) {
    if (
      !window.confirm(
        `Clone v${version.version} into a new draft for editing? Live published version will not change until you publish.`,
      )
    ) {
      return;
    }
    setBusy("restore");
    setBanner(null);
    try {
      const restored = await restoreTeamVersion(
        await getAccessToken(),
        initial.id,
        version.id,
        { asDraft: true },
      );
      applyTeam(restored);
      setViewing(null);
      setBanner(`Loaded v${version.version} into draft v${restored.draftVersion}`);
      void refreshVersions();
    } catch (err) {
      setBanner(err instanceof Error ? err.message : "Restore failed");
    } finally {
      setBusy(null);
    }
  }

  const historyItems: VersionHistoryItem[] = versions.map((version) => ({
    id: version.id,
    version: version.version,
    status: version.status,
    isLive: version.isLive,
    createdAt: version.createdAt,
    details: [version.mode, `${version.memberCount} members`],
  }));

  return (
    <div className="space-y-3">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <div className="flex min-w-0 items-center gap-1.5">
            <BackLink href="/admin/teams" label="Back to teams" />
            <h1 className="truncate font-display text-2xl font-semibold tracking-tight">
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
          <div>
            <Label htmlFor="team-model">Leader model</Label>
            <Select
              id="team-model"
              value={form.model}
              onChange={(event) => update("model", event.target.value as ModelId)}
            >
              {ALLOWED_MODELS.map((model) => (
                <option key={model.id} value={model.id}>
                  {model.label}
                </option>
              ))}
            </Select>
          </div>
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

      <section className="rounded-xl border border-line bg-raised/40 p-4">
        <div className="mb-3 flex items-center justify-between gap-2">
          <h2 className="text-sm font-semibold">Members</h2>
          <Badge
            tone={form.memberConfigIds.length >= 2 ? "success" : "warning"}
          >
            {form.memberConfigIds.length} selected
          </Badge>
        </div>
        <p className="mb-3 text-xs text-slate-muted">
          Need at least two published agents. Order is routing priority.
        </p>

        {form.memberConfigIds.length === 0 ? (
          <p className="mb-3 rounded-md border border-dashed border-line px-3 py-4 text-center text-sm text-slate-muted">
            No members yet — add agents below.
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

      <VersionHistoryPanel
        versions={historyItems}
        busy={busy !== null}
        onRefresh={() => void refreshVersions()}
        onView={(version) => void viewVersion(version)}
        onRestoreLive={(version) => void restoreLive(version)}
        onRestoreDraft={(version) => void restoreDraft(version)}
        onCloseView={() => setViewing(null)}
        viewing={
          viewing ? (
            <dl className="grid gap-2 text-sm sm:grid-cols-2">
              <div>
                <dt className="text-xs text-slate-muted">Mode</dt>
                <dd>{viewing.mode}</dd>
              </div>
              <div>
                <dt className="text-xs text-slate-muted">Model</dt>
                <dd>{viewing.model}</dd>
              </div>
              <div className="sm:col-span-2">
                <dt className="text-xs text-slate-muted">Instructions</dt>
                <dd className="mt-0.5 whitespace-pre-wrap font-mono text-[13px]">
                  {viewing.instructions}
                </dd>
              </div>
              <div className="sm:col-span-2">
                <dt className="mb-1 text-xs text-slate-muted">Members</dt>
                <dd>
                  <ul className="space-y-1">
                    {viewing.members.map((member) => (
                      <li key={member.agentConfigId} className="text-sm">
                        {member.position + 1}. {member.name}{" "}
                        <span className="text-xs text-slate-muted">
                          (agent v{member.version})
                        </span>
                      </li>
                    ))}
                  </ul>
                </dd>
              </div>
            </dl>
          ) : null
        }
      />
    </div>
  );
}
