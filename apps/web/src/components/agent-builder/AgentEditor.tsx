"use client";

import { useMemo, useState } from "react";

import { PreviewChat } from "@/components/agent-builder/PreviewChat";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import {
  FieldError,
  Input,
  Label,
  Select,
  Textarea,
} from "@/components/ui/Field";
import {
  publishAgent,
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
  const [form, setForm] = useState<AgentDraftInput>({
    name: initial.name,
    slug: initial.slug,
    description: initial.description,
    instructions: initial.instructions,
    model: initial.model,
    temperature: initial.temperature,
    memoryMode: initial.memoryMode,
    tools: initial.tools,
    knowledgeBaseId: initial.knowledgeBaseId,
  });
  const [status, setStatus] = useState(initial.status);
  const [publishedVersion, setPublishedVersion] = useState(
    initial.publishedVersion,
  );
  const [draftVersion, setDraftVersion] = useState(initial.draftVersion);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [banner, setBanner] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [publishing, setPublishing] = useState(false);

  const sources = initial.knowledgeBase?.sources ?? [];

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
    } catch (err) {
      setBanner(err instanceof Error ? err.message : "Publish failed");
    } finally {
      setPublishing(false);
    }
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.14em] text-teal">
            Agent editor
          </p>
          <h1 className="font-display text-3xl font-semibold tracking-tight">
            {form.name || "Untitled agent"}
          </h1>
          <div className="mt-2 flex flex-wrap items-center gap-2 text-sm text-slate-muted">
            <Badge tone={status === "published" ? "success" : "warning"}>
              {status}
            </Badge>
            <span>draft v{draftVersion}</span>
            {publishedVersion ? <span>live v{publishedVersion}</span> : null}
            <span>updated {formatRelative(initial.updatedAt)}</span>
          </div>
        </div>
        <div className="flex gap-2">
          <Button variant="secondary" onClick={onSave} disabled={saving}>
            {saving ? "Saving…" : "Save draft"}
          </Button>
          <Button variant="accent" onClick={onPublish} disabled={publishing}>
            {publishing ? "Publishing…" : "Publish"}
          </Button>
        </div>
      </div>

      {banner ? (
        <div className="rounded-lg border border-teal/30 bg-teal/10 px-3 py-2 text-sm text-ink-soft">
          {banner}
        </div>
      ) : null}

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1.1fr)_minmax(320px,0.9fr)]">
        <div className="space-y-5">
          <section className="surface-panel rounded-2xl p-5">
            <h2 className="font-display text-lg font-semibold">Identity</h2>
            <div className="mt-4 grid gap-4 md:grid-cols-2">
              <div>
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
              <div className="md:col-span-2">
                <Label htmlFor="description">Description</Label>
                <Input
                  id="description"
                  value={form.description}
                  onChange={(e) => update("description", e.target.value)}
                />
              </div>
            </div>
          </section>

          <section className="surface-panel rounded-2xl p-5">
            <h2 className="font-display text-lg font-semibold">Instructions</h2>
            <div className="mt-4">
              <Label htmlFor="instructions" hint="shown to the model">
                System prompt
              </Label>
              <Textarea
                id="instructions"
                value={form.instructions}
                onChange={(e) => update("instructions", e.target.value)}
                className="min-h-44 font-mono text-[13px]"
              />
              <FieldError message={fieldErrors.instructions} />
            </div>
          </section>

          <section className="surface-panel rounded-2xl p-5">
            <h2 className="font-display text-lg font-semibold">Model</h2>
            <div className="mt-4 grid gap-4 md:grid-cols-2">
              <div>
                <Label htmlFor="model">Allowlisted model</Label>
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
                  className="w-full accent-teal"
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
            </div>
          </section>

          <section className="surface-panel rounded-2xl p-5">
            <h2 className="font-display text-lg font-semibold">Tools</h2>
            <ul className="mt-4 space-y-3">
              {TOOL_CATALOG.map((tool) => {
                const binding = toolMap.get(tool.kind);
                const enabled = Boolean(binding?.enabled);
                return (
                  <li
                    key={tool.kind}
                    className="flex items-start justify-between gap-4 rounded-xl border border-line bg-raised/70 px-4 py-3"
                  >
                    <div>
                      <p className="font-medium text-ink">{tool.label}</p>
                      <p className="text-sm text-slate-muted">{tool.description}</p>
                      {tool.requiresApproval ? (
                        <Badge tone="warning" className="mt-2">
                          requires approval
                        </Badge>
                      ) : null}
                      {enabled && tool.kind.startsWith("rest_") ? (
                        <div className="mt-3 grid gap-2 sm:grid-cols-2">
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
                    </div>
                    <label className="flex items-center gap-2 text-sm">
                      <input
                        type="checkbox"
                        checked={enabled}
                        onChange={(e) => toggleTool(tool.kind, e.target.checked)}
                        className="size-4 accent-teal"
                      />
                      Enable
                    </label>
                  </li>
                );
              })}
            </ul>
            <div className="mt-5 border-t border-line pt-4">
              <p className="text-xs font-semibold uppercase tracking-[0.1em] text-slate-muted">
                Tenant tools
              </p>
              <ul className="mt-3 space-y-2">
                {toolDefinitions.map((definition) => {
                  const binding = reusableMap.get(definition.id);
                  const enabled = Boolean(binding?.enabled);
                  return (
                    <li
                      key={definition.id}
                      className="flex items-start justify-between gap-4 rounded-xl border border-line bg-raised/70 px-4 py-3"
                    >
                      <div>
                        <p className="font-medium">{definition.name}</p>
                        <p className="text-sm text-slate-muted">
                          {reusableSummary(definition)}
                        </p>
                        <div className="mt-2 flex flex-wrap gap-2">
                          {(definition.approvalRequired ||
                            (definition.kind === "http" &&
                              definition.httpMethod !== "GET")) && (
                            <Badge tone="warning">requires approval</Badge>
                          )}
                          {definition.credentialId ? (
                            <Badge tone="info">
                              credential:{" "}
                              {credentialMap.get(definition.credentialId) ??
                                "configured"}
                            </Badge>
                          ) : (
                            <Badge tone="neutral">no credential</Badge>
                          )}
                          {!definition.active ? (
                            <Badge tone="neutral">inactive</Badge>
                          ) : null}
                        </div>
                      </div>
                      <label className="flex items-center gap-2 text-sm">
                        <input
                          type="checkbox"
                          checked={enabled}
                          disabled={!definition.active}
                          onChange={(event) =>
                            toggleReusable(definition, event.target.checked)
                          }
                          className="size-4 accent-teal"
                        />
                        Attach
                      </label>
                    </li>
                  );
                })}
                {toolDefinitions.length === 0 ? (
                  <li className="rounded-lg border border-dashed border-line px-3 py-5 text-center text-sm text-slate-muted">
                    Create reusable tools from Admin → Tools.
                  </li>
                ) : null}
              </ul>
            </div>
          </section>

          <section className="surface-panel rounded-2xl p-5">
            <div className="flex items-center justify-between gap-3">
              <h2 className="font-display text-lg font-semibold">Knowledge</h2>
              <Badge tone="info">
                {initial.knowledgeBase?.name ?? "No KB linked"}
              </Badge>
            </div>
            <p className="mt-2 text-sm text-slate-muted">
              Sources ingest asynchronously. Preview chat can use draft bindings;
              customer sessions pin published versions.
            </p>
            <ul className="mt-4 space-y-2">
              {sources.map((source) => (
                <li
                  key={source.id}
                  className="flex items-center justify-between gap-3 rounded-lg border border-line px-3 py-2"
                >
                  <div>
                    <p className="text-sm font-medium">{source.name}</p>
                    <p className="text-xs text-slate-muted">
                      {formatBytes(source.byteSize)} · {source.mimeType}
                      {source.errorMessage ? ` · ${source.errorMessage}` : ""}
                    </p>
                  </div>
                  <Badge tone={ingestionTone(source.status)}>{source.status}</Badge>
                </li>
              ))}
              {sources.length === 0 ? (
                <li className="rounded-lg border border-dashed border-line px-3 py-6 text-center text-sm text-slate-muted">
                  Upload documents from the Ingestion page once a knowledge base is
                  attached.
                </li>
              ) : null}
            </ul>
            <div className="mt-4">
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

        <PreviewChat agentId={initial.id} agentName={form.name} />
      </div>
    </div>
  );
}
