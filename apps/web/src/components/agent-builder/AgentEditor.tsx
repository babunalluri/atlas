"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";

import { BackLink } from "@/components/ui/BackLink";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { EditorActions } from "@/components/ui/EditorActions";
import {
  FieldError,
  Input,
  Label,
  Select,
  Textarea,
} from "@/components/ui/Field";
import { PublishIcon, SaveIcon, TrashIcon } from "@/components/ui/icons";
import {
  VersionHistoryPanel,
  type VersionHistoryItem,
} from "@/components/ui/VersionHistoryPanel";
import {
  deleteAgent,
  getAgentVersion,
  listAgentVersions,
  publishAgent,
  restoreAgentVersion,
  saveAgentDraft,
  type CredentialSummary,
} from "@/lib/api/admin";
import {
  ALLOWED_MODELS,
  TOOL_CATALOG,
  type AgentConfig,
  type AgentDraftInput,
  type MemoryMode,
  type ModelId,
  type ToolBinding,
  type ToolDefinition,
} from "@/lib/api/types";
import { useAgentOsToken } from "@/lib/auth/token";
import { formatBytes, formatRelative } from "@/lib/utils";
import {
  slugifyName,
  validateAgentDraft,
} from "@/lib/validation/agent-form";

function ingestionTone(status: string) {
  if (status === "ready") return "success" as const;
  if (status === "failed") return "danger" as const;
  if (status === "processing" || status === "uploading") return "warning" as const;
  return "neutral" as const;
}

function applyAgentToForm(agent: AgentConfig): AgentDraftInput {
  return {
    name: agent.name,
    slug: agent.slug,
    description: agent.description,
    instructions: agent.instructions,
    model: agent.model,
    temperature: agent.temperature,
    memoryMode: agent.memoryMode,
    tools: agent.tools,
    knowledgeBaseId: agent.knowledgeBaseId,
  };
}

export function AgentEditor({
  initial,
  toolDefinitions = [],
  credentials = [],
}: {
  initial: AgentConfig;
  toolDefinitions?: ToolDefinition[];
  credentials?: CredentialSummary[];
}) {
  const { getAccessToken } = useAgentOsToken();
  const router = useRouter();
  const [form, setForm] = useState<AgentDraftInput>(() =>
    applyAgentToForm(initial),
  );
  const [status, setStatus] = useState(initial.status);
  const [publishedVersion, setPublishedVersion] = useState(
    initial.publishedVersion,
  );
  const [draftVersion, setDraftVersion] = useState(initial.draftVersion);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [banner, setBanner] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [publishing, setPublishing] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [versionBusy, setVersionBusy] = useState(false);
  const [versions, setVersions] = useState<VersionHistoryItem[]>([]);
  const [viewing, setViewing] = useState<{
    version: number;
    instructions: string;
    modelId: string;
    temperature: number;
    memoryMode: string;
  } | null>(null);
  const [toolQuery, setToolQuery] = useState("");
  const [toolScope, setToolScope] = useState<"all" | "enabled" | "builtin" | "tenant">(
    "all",
  );
  const [toolPage, setToolPage] = useState(1);

  const sources = initial.knowledgeBase?.sources ?? [];
  const toolPageSize = 25;

  const toolMap = useMemo(() => {
    const map = new Map(
      form.tools.filter((tool) => !tool.definitionId).map((t) => [t.kind, t]),
    );
    return map;
  }, [form.tools]);
  const reusableMap = useMemo(
    () =>
      new Map(
        form.tools
          .filter((tool) => tool.definitionId)
          .map((tool) => [tool.definitionId as string, tool]),
      ),
    [form.tools],
  );
  const credentialMap = useMemo(
    () => new Map(credentials.map((credential) => [credential.id, credential.name])),
    [credentials],
  );

  const enabledToolCount = useMemo(
    () => form.tools.filter((tool) => tool.enabled).length,
    [form.tools],
  );

  const filteredBuiltinTools = useMemo(() => {
    const q = toolQuery.trim().toLowerCase();
    return TOOL_CATALOG.filter((tool) => {
      const enabled = Boolean(toolMap.get(tool.kind)?.enabled);
      if (toolScope === "tenant") return false;
      if (toolScope === "enabled" && !enabled) return false;
      if (!q) return true;
      return (
        tool.label.toLowerCase().includes(q) ||
        tool.description.toLowerCase().includes(q) ||
        tool.kind.toLowerCase().includes(q)
      );
    });
  }, [toolMap, toolQuery, toolScope]);

  const filteredTenantTools = useMemo(() => {
    const q = toolQuery.trim().toLowerCase();
    return toolDefinitions.filter((definition) => {
      const enabled = Boolean(reusableMap.get(definition.id)?.enabled);
      if (toolScope === "builtin") return false;
      if (toolScope === "enabled" && !enabled) return false;
      if (!q) return true;
      const haystack = [
        definition.name,
        definition.slug,
        definition.kind,
        definition.httpMethod ?? "",
        definition.baseUrl ?? "",
        definition.path ?? "",
      ]
        .join(" ")
        .toLowerCase();
      return haystack.includes(q);
    });
  }, [reusableMap, toolDefinitions, toolQuery, toolScope]);

  const toolTotal = filteredBuiltinTools.length + filteredTenantTools.length;
  const toolTotalPages = Math.max(1, Math.ceil(toolTotal / toolPageSize));
  const safeToolPage = Math.min(toolPage, toolTotalPages);
  const toolSliceStart = (safeToolPage - 1) * toolPageSize;

  const pagedBuiltin = useMemo(() => {
    // Builtin tools occupy the start of the combined list.
    const end = toolSliceStart + toolPageSize;
    if (toolSliceStart >= filteredBuiltinTools.length) return [];
    return filteredBuiltinTools.slice(toolSliceStart, end);
  }, [filteredBuiltinTools, toolSliceStart, toolPageSize]);

  const pagedTenant = useMemo(() => {
    const builtinLen = filteredBuiltinTools.length;
    const start = Math.max(0, toolSliceStart - builtinLen);
    const end = Math.max(0, toolSliceStart + toolPageSize - builtinLen);
    if (end <= 0) return [];
    return filteredTenantTools.slice(start, end);
  }, [
    filteredBuiltinTools.length,
    filteredTenantTools,
    toolSliceStart,
    toolPageSize,
  ]);

  function update<K extends keyof AgentDraftInput>(
    key: K,
    value: AgentDraftInput[K],
  ) {
    setForm((prev) => ({ ...prev, [key]: value }));
  }

  function toggleTool(kind: ToolBinding["kind"], enabled: boolean) {
    const catalog = TOOL_CATALOG.find((t) => t.kind === kind);
    if (!catalog) return;
    setForm((prev) => {
      const existing = prev.tools.find((t) => t.kind === kind);
      if (existing) {
        return {
          ...prev,
          tools: prev.tools.map((t) =>
            t.kind === kind ? { ...t, enabled } : t,
          ),
        };
      }
      if (!enabled) return prev;
      const next: ToolBinding = {
        id: `tool_${kind}`,
        kind,
        label: catalog.label,
        enabled: true,
        config: {},
        requiresApproval: catalog.requiresApproval,
      };
      return { ...prev, tools: [...prev.tools, next] };
    });
  }

  function updateToolConfig(
    kind: ToolBinding["kind"],
    key: string,
    value: string,
  ) {
    setForm((prev) => ({
      ...prev,
      tools: prev.tools.map((tool) =>
        tool.kind === kind
          ? { ...tool, config: { ...tool.config, [key]: value } }
          : tool,
      ),
    }));
  }

  function toggleReusable(definition: ToolDefinition, enabled: boolean) {
    setForm((previous) => {
      const existing = previous.tools.find(
        (tool) => tool.definitionId === definition.id,
      );
      if (existing) {
        return {
          ...previous,
          tools: previous.tools.map((tool) =>
            tool.definitionId === definition.id ? { ...tool, enabled } : tool,
          ),
        };
      }
      if (!enabled) return previous;
      return {
        ...previous,
        tools: [
          ...previous.tools,
          {
            id: `definition_${definition.id}`,
            kind:
              definition.kind === "http" && definition.httpMethod === "GET"
                ? "rest_read"
                : "rest_mutate",
            definitionId: definition.id,
            label: definition.name,
            enabled: true,
            config: {},
            requiresApproval:
              definition.approvalRequired ||
              (definition.kind === "http" && definition.httpMethod !== "GET"),
          },
        ],
      };
    });
  }

  function reusableSummary(definition: ToolDefinition): string {
    if (definition.kind === "http") {
      return `${definition.httpMethod ?? "HTTP"} ${definition.baseUrl ?? ""}${
        definition.path ?? ""
      }`;
    }
    const selected =
      (definition.config.allowed_operations as string[] | undefined) ??
      (definition.config.include_tools as string[] | undefined) ??
      [];
    return `${definition.kind.replace("_", " ")} · ${selected.length} selected capabilities`;
  }

  async function onSave() {
    setBanner(null);
    const parsed = validateAgentDraft(form);
    if (!parsed.success) {
      const errs: Record<string, string> = {};
      for (const issue of parsed.error.issues) {
        const key = issue.path[0]?.toString() ?? "form";
        errs[key] = issue.message;
      }
      setFieldErrors(errs);
      return;
    }
    setFieldErrors({});
    setSaving(true);
    try {
      const token = await getAccessToken();
      const saved = await saveAgentDraft(token, initial.id, parsed.data);
      setStatus(saved.status);
      setDraftVersion(saved.draftVersion);
      setBanner("Draft saved");
      void refreshVersions();
    } catch (err) {
      setBanner(err instanceof Error ? err.message : "Save failed");
    } finally {
      setSaving(false);
    }
  }

  async function onPublish() {
    setPublishing(true);
    setBanner(null);
    try {
      const token = await getAccessToken();
      await saveAgentDraft(token, initial.id, form);
      const published = await publishAgent(token, initial.id);
      setStatus(published.status);
      setPublishedVersion(published.publishedVersion);
      setDraftVersion(published.draftVersion);
      setBanner(`Published v${published.publishedVersion}`);
      void refreshVersions();
    } catch (err) {
      setBanner(err instanceof Error ? err.message : "Publish failed");
    } finally {
      setPublishing(false);
    }
  }

  async function onDelete() {
    if (
      !window.confirm(
        `Delete “${form.name || "this agent"}”? This cannot be undone.`,
      )
    ) {
      return;
    }
    setDeleting(true);
    setBanner(null);
    try {
      await deleteAgent(await getAccessToken(), initial.id);
      router.push("/admin/agents");
    } catch (err) {
      setBanner(err instanceof Error ? err.message : "Delete failed");
      setDeleting(false);
    }
  }

  function applyAgent(agent: AgentConfig) {
    setForm(applyAgentToForm(agent));
    setStatus(agent.status);
    setDraftVersion(agent.draftVersion);
    setPublishedVersion(agent.publishedVersion);
  }

  async function refreshVersions() {
    setVersionBusy(true);
    try {
      const rows = await listAgentVersions(await getAccessToken(), initial.id);
      setVersions(
        rows.map((row) => ({
          id: row.id,
          version: row.version,
          status: row.status,
          isLive: row.isLive,
          createdAt: row.createdAt,
          details: [row.modelId],
        })),
      );
    } catch (err) {
      setBanner(err instanceof Error ? err.message : "Failed to load versions");
    } finally {
      setVersionBusy(false);
    }
  }

  useEffect(() => {
    void refreshVersions();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- intentional mount-only
  }, [initial.id]);

  async function viewVersion(version: VersionHistoryItem) {
    setVersionBusy(true);
    try {
      const detail = await getAgentVersion(
        await getAccessToken(),
        initial.id,
        version.id,
      );
      setViewing({
        version: detail.version,
        instructions: detail.instructions,
        modelId: detail.modelId,
        temperature: detail.temperature,
        memoryMode: detail.memoryMode,
      });
    } catch (err) {
      setBanner(err instanceof Error ? err.message : "Failed to load version");
    } finally {
      setVersionBusy(false);
    }
  }

  async function restoreLive(version: VersionHistoryItem) {
    if (version.isLive) return;
    if (
      !window.confirm(
        `Make v${version.version} the live published version? Current live stays available in history.`,
      )
    ) {
      return;
    }
    setVersionBusy(true);
    setBanner(null);
    try {
      const restored = await restoreAgentVersion(
        await getAccessToken(),
        initial.id,
        version.id,
      );
      applyAgent(restored);
      setViewing(null);
      setBanner(`Restored live to v${restored.publishedVersion}`);
      void refreshVersions();
    } catch (err) {
      setBanner(err instanceof Error ? err.message : "Restore failed");
    } finally {
      setVersionBusy(false);
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
    setVersionBusy(true);
    setBanner(null);
    try {
      const restored = await restoreAgentVersion(
        await getAccessToken(),
        initial.id,
        version.id,
        { asDraft: true },
      );
      applyAgent(restored);
      setViewing(null);
      setBanner(`Loaded v${version.version} into draft v${restored.draftVersion}`);
      void refreshVersions();
    } catch (err) {
      setBanner(err instanceof Error ? err.message : "Restore failed");
    } finally {
      setVersionBusy(false);
    }
  }

  return (
    <div className="space-y-3">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <div className="flex min-w-0 items-center gap-1.5">
            <BackLink href="/admin/agents" label="Back to agents" />
            <h1 className="truncate font-display text-2xl font-semibold tracking-tight">
              {form.name || "Untitled agent"}
            </h1>
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-slate-muted">
            <Badge tone={status === "published" ? "success" : "warning"}>
              {status}
            </Badge>
            <span>draft v{draftVersion}</span>
            {publishedVersion ? <span>live v{publishedVersion}</span> : null}
            <span>/{form.slug}</span>
            <span>{formatRelative(initial.updatedAt)}</span>
          </div>
        </div>
        <EditorActions>
          <Button
            variant="danger"
            size="sm"
            onClick={() => void onDelete()}
            disabled={saving || publishing || deleting}
          >
            <TrashIcon />
            {deleting ? "Deleting…" : "Delete"}
          </Button>
          <Button
            variant="secondary"
            size="sm"
            onClick={onSave}
            disabled={saving || publishing || deleting}
          >
            <SaveIcon />
            {saving ? "Saving…" : "Save"}
          </Button>
          <Button
            variant="accent"
            size="sm"
            onClick={onPublish}
            disabled={saving || publishing || deleting}
          >
            <PublishIcon />
            {publishing ? "Publishing…" : "Publish"}
          </Button>
        </EditorActions>
      </header>

      {banner ? (
        <p className="rounded-md border border-teal/30 bg-teal/10 px-3 py-1.5 text-sm">
          {banner}
        </p>
      ) : null}

      <div className="space-y-3">
          <section className="rounded-xl border border-line bg-raised/40 p-4">
            <div className="grid gap-3 md:grid-cols-3">
              <div className="md:col-span-2">
                <Label htmlFor="name">Name</Label>
                <Input
                  id="name"
                  value={form.name}
                  onChange={(e) => {
                    const next = e.target.value;
                    update("name", next);
                    if (!form.slug || form.slug === slugifyName(form.name)) {
                      update("slug", slugifyName(next));
                    }
                  }}
                />
                <FieldError message={fieldErrors.name} />
              </div>
              <div>
                <Label htmlFor="slug">Slug</Label>
                <Input
                  id="slug"
                  value={form.slug}
                  onChange={(e) => update("slug", e.target.value)}
                />
                <FieldError message={fieldErrors.slug} />
              </div>
              <div className="md:col-span-3">
                <Label htmlFor="description">Description</Label>
                <Input
                  id="description"
                  value={form.description}
                  onChange={(e) => update("description", e.target.value)}
                  placeholder="Optional"
                />
              </div>
              <div>
                <Label htmlFor="model">Model</Label>
                <Select
                  id="model"
                  value={form.model}
                  onChange={(e) => update("model", e.target.value as ModelId)}
                >
                  {ALLOWED_MODELS.map((m) => (
                    <option key={m.id} value={m.id}>
                      {m.label}
                    </option>
                  ))}
                </Select>
              </div>
              <div>
                <Label htmlFor="temperature" hint={`${form.temperature.toFixed(2)}`}>
                  Temperature
                </Label>
                <input
                  id="temperature"
                  type="range"
                  min={0}
                  max={1.5}
                  step={0.05}
                  value={form.temperature}
                  onChange={(e) =>
                    update("temperature", Number.parseFloat(e.target.value))
                  }
                  className="mt-2 w-full accent-teal"
                />
              </div>
              <div>
                <Label htmlFor="memory">Memory</Label>
                <Select
                  id="memory"
                  value={form.memoryMode}
                  onChange={(e) =>
                    update("memoryMode", e.target.value as MemoryMode)
                  }
                >
                  <option value="session">Session only</option>
                  <option value="persistent">Persistent user memory</option>
                </Select>
              </div>
              <div className="md:col-span-3">
                <Label htmlFor="instructions">Instructions</Label>
                <Textarea
                  id="instructions"
                  value={form.instructions}
                  onChange={(e) => update("instructions", e.target.value)}
                  rows={5}
                  className="min-h-0 font-mono text-[13px]"
                />
                <FieldError message={fieldErrors.instructions} />
              </div>
            </div>
          </section>

          <section className="rounded-xl border border-line bg-raised/40 p-4">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
              <h2 className="text-sm font-semibold">Tools</h2>
              <Badge tone={enabledToolCount > 0 ? "success" : "neutral"}>
                {enabledToolCount} enabled
              </Badge>
            </div>

            <div className="mb-3 flex flex-wrap items-center gap-2">
              <Input
                value={toolQuery}
                placeholder="Search tools…"
                className="min-w-[180px] flex-1"
                onChange={(event) => {
                  setToolQuery(event.target.value);
                  setToolPage(1);
                }}
              />
              {(
                [
                  ["all", "All"],
                  ["enabled", "Enabled"],
                  ["builtin", "Built-in"],
                  ["tenant", "Tenant"],
                ] as const
              ).map(([value, label]) => (
                <button
                  key={value}
                  type="button"
                  onClick={() => {
                    setToolScope(value);
                    setToolPage(1);
                  }}
                  className={
                    toolScope === value
                      ? "rounded-md bg-ink px-2.5 py-1.5 text-xs font-medium text-canvas"
                      : "rounded-md bg-raised px-2.5 py-1.5 text-xs font-medium text-slate-muted hover:bg-mist"
                  }
                >
                  {label}
                </button>
              ))}
            </div>

            <ul className="max-h-80 divide-y divide-line overflow-y-auto rounded-md border border-line">
              {pagedBuiltin.map((tool) => {
                const binding = toolMap.get(tool.kind);
                const enabled = Boolean(binding?.enabled);
                return (
                  <li key={tool.kind} className="bg-canvas/30 px-3 py-2">
                    <div className="flex items-center gap-3">
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <p className="truncate text-sm font-medium text-ink">
                            {tool.label}
                          </p>
                          <span className="text-[10px] uppercase tracking-wide text-slate-muted">
                            built-in
                          </span>
                          {tool.requiresApproval ? (
                            <Badge tone="warning">approval</Badge>
                          ) : null}
                        </div>
                        <p className="truncate text-xs text-slate-muted" title={tool.description}>
                          {tool.description}
                        </p>
                      </div>
                      <label className="flex shrink-0 items-center gap-1.5 text-xs">
                        <input
                          type="checkbox"
                          checked={enabled}
                          onChange={(e) => toggleTool(tool.kind, e.target.checked)}
                          className="size-4 accent-teal"
                        />
                        On
                      </label>
                    </div>
                    {enabled && tool.kind.startsWith("rest_") ? (
                      <div className="mt-2 grid gap-2 sm:grid-cols-2">
                        <Input
                          aria-label={`${tool.label} base URL`}
                          value={String(binding?.config.base_url ?? "")}
                          placeholder="https://api.example.com/v1"
                          onChange={(event) =>
                            updateToolConfig(
                              tool.kind,
                              "base_url",
                              event.target.value,
                            )
                          }
                        />
                        <Input
                          aria-label={`${tool.label} credential ID`}
                          value={String(binding?.config.credential_id ?? "")}
                          placeholder="Optional credential UUID"
                          onChange={(event) =>
                            updateToolConfig(
                              tool.kind,
                              "credential_id",
                              event.target.value,
                            )
                          }
                        />
                      </div>
                    ) : null}
                  </li>
                );
              })}

              {pagedTenant.map((definition) => {
                const enabled = Boolean(reusableMap.get(definition.id)?.enabled);
                const summary = reusableSummary(definition);
                const needsApproval =
                  definition.approvalRequired ||
                  (definition.kind === "http" &&
                    definition.httpMethod !== "GET");
                return (
                  <li
                    key={definition.id}
                    className="flex items-center gap-3 bg-canvas/30 px-3 py-2"
                  >
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <p className="truncate text-sm font-medium">
                          {definition.name}
                        </p>
                        {needsApproval ? (
                          <Badge tone="warning">approval</Badge>
                        ) : null}
                        {!definition.active ? (
                          <Badge tone="neutral">inactive</Badge>
                        ) : null}
                      </div>
                      <p className="truncate text-xs text-slate-muted" title={summary}>
                        {summary}
                        {definition.credentialId
                          ? ` · ${credentialMap.get(definition.credentialId) ?? "credential"}`
                          : ""}
                      </p>
                    </div>
                    <label className="flex shrink-0 items-center gap-1.5 text-xs">
                      <input
                        type="checkbox"
                        checked={enabled}
                        disabled={!definition.active}
                        onChange={(event) =>
                          toggleReusable(definition, event.target.checked)
                        }
                        className="size-4 accent-teal"
                      />
                      On
                    </label>
                  </li>
                );
              })}

              {toolTotal === 0 ? (
                <li className="px-3 py-6 text-center text-sm text-slate-muted">
                  {toolDefinitions.length === 0 && toolScope !== "builtin"
                    ? "No tenant tools yet — create them under Admin → Tools."
                    : "No tools match this search."}
                </li>
              ) : null}
            </ul>

            <div className="mt-2 flex flex-wrap items-center justify-between gap-2 text-xs text-slate-muted">
              <p>
                {toolTotal === 0
                  ? "0 tools"
                  : `Showing ${toolSliceStart + 1}–${Math.min(toolTotal, toolSliceStart + toolPageSize)} of ${toolTotal}`}
              </p>
              <div className="flex items-center gap-2">
                <Button
                  size="sm"
                  variant="secondary"
                  disabled={safeToolPage <= 1}
                  onClick={() => setToolPage((page) => Math.max(1, page - 1))}
                >
                  Previous
                </Button>
                <span className="mono-cell">
                  {safeToolPage} / {toolTotalPages}
                </span>
                <Button
                  size="sm"
                  variant="secondary"
                  disabled={safeToolPage >= toolTotalPages}
                  onClick={() =>
                    setToolPage((page) => Math.min(toolTotalPages, page + 1))
                  }
                >
                  Next
                </Button>
              </div>
            </div>
          </section>

          <section className="rounded-xl border border-line bg-raised/40 p-4">
            <div className="mb-3 flex items-center justify-between gap-2">
              <h2 className="text-sm font-semibold">Knowledge</h2>
              <Badge tone="info">
                {initial.knowledgeBase?.name ?? "No KB linked"}
              </Badge>
            </div>
            <ul className="space-y-1.5">
              {sources.map((source) => (
                <li
                  key={source.id}
                  className="flex items-center justify-between gap-3 rounded-md border border-line px-2.5 py-2"
                >
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium">{source.name}</p>
                    <p className="text-xs text-slate-muted">
                      {formatBytes(source.byteSize)} · {source.mimeType}
                      {source.errorMessage ? ` · ${source.errorMessage}` : ""}
                    </p>
                  </div>
                  <Badge tone={ingestionTone(source.status)}>{source.status}</Badge>
                </li>
              ))}
              {sources.length === 0 ? (
                <li className="rounded-md border border-dashed border-line px-3 py-3 text-center text-sm text-slate-muted">
                  No sources yet — attach a KB and upload from Ingestion.
                </li>
              ) : null}
            </ul>
            <div className="mt-3">
              <Label htmlFor="kb">Knowledge base ID</Label>
              <Input
                id="kb"
                value={form.knowledgeBaseId ?? ""}
                placeholder="kb_..."
                onChange={(e) =>
                  update("knowledgeBaseId", e.target.value || null)
                }
              />
            </div>
          </section>
      </div>

      <VersionHistoryPanel
        versions={versions}
        busy={versionBusy || saving || publishing || deleting}
        onRefresh={() => void refreshVersions()}
        onView={(version) => void viewVersion(version)}
        onRestoreLive={(version) => void restoreLive(version)}
        onRestoreDraft={(version) => void restoreDraft(version)}
        onCloseView={() => setViewing(null)}
        viewing={
          viewing ? (
            <dl className="grid gap-2 text-sm sm:grid-cols-2">
              <div>
                <dt className="text-xs text-slate-muted">Model</dt>
                <dd>{viewing.modelId}</dd>
              </div>
              <div>
                <dt className="text-xs text-slate-muted">Temperature</dt>
                <dd>{viewing.temperature}</dd>
              </div>
              <div>
                <dt className="text-xs text-slate-muted">Memory</dt>
                <dd>{viewing.memoryMode}</dd>
              </div>
              <div className="sm:col-span-2">
                <dt className="text-xs text-slate-muted">Instructions</dt>
                <dd className="mt-0.5 whitespace-pre-wrap font-mono text-[13px]">
                  {viewing.instructions}
                </dd>
              </div>
            </dl>
          ) : null
        }
      />
    </div>
  );
}
