"use client";

import { useMemo, useState } from "react";
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
import { ModelSelect } from "@/components/ui/ModelSelect";
import { PublishIcon, SaveIcon, TrashIcon } from "@/components/ui/icons";
import { ToolAttachmentSection } from "@/components/tools/ToolAttachmentSection";
import {
  deleteAgent,
  publishAgent,
  saveAgentDraft,
  type CredentialSummary,
} from "@/lib/api/admin";
import {
  type AgentConfig,
  type AgentDraftInput,
  type FrameworkAdapter,
  type MemoryMode,
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
    frameworkAdapter: agent.frameworkAdapter ?? "agno",
    guardrails: agent.guardrails ?? {
      promptInjection: false,
      piiDetection: false,
      openaiModeration: false,
    },
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

  const sources = initial.knowledgeBase?.sources ?? [];

  const credentialNames = useMemo(
    () =>
      Object.fromEntries(
        credentials.map((credential) => [credential.id, credential.name]),
      ),
    [credentials],
  );

  function update<K extends keyof AgentDraftInput>(
    key: K,
    value: AgentDraftInput[K],
  ) {
    setForm((prev) => ({ ...prev, [key]: value }));
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
    setPublishing(true);
    try {
      const token = await getAccessToken();
      await saveAgentDraft(token, initial.id, parsed.data);
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

  return (
    <div className="space-y-3">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <div className="flex min-w-0 items-center gap-1.5">
            <BackLink href="/admin/agents" label="Back to agents" />
            <h1 className="min-w-0 truncate py-0.5 font-display text-2xl font-semibold leading-snug tracking-tight">
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
              <ModelSelect
                id="model"
                value={form.model}
                onChange={(model) => update("model", model)}
                credentials={credentials}
              />
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
              <div className="md:col-span-3 rounded-lg border border-line bg-canvas/40 p-3">
                <p className="text-sm font-semibold">Advanced</p>
                <p className="mt-1 text-xs text-slate-muted">
                  Framework adapters wrap external runtimes when configured;
                  unsupported adapters fail at run time with HTTP 400.
                </p>
                <div className="mt-3 max-w-sm">
                  <Label htmlFor="framework-adapter">Framework adapter</Label>
                  <Select
                    id="framework-adapter"
                    value={form.frameworkAdapter}
                    onChange={(e) =>
                      update(
                        "frameworkAdapter",
                        e.target.value as FrameworkAdapter,
                      )
                    }
                  >
                    <option value="agno">Native (default)</option>
                    <option value="langgraph">LangGraph</option>
                    <option value="dspy">DSPy</option>
                    <option value="claude_agent_sdk">Claude Agent SDK</option>
                    <option value="antigravity">Antigravity</option>
                  </Select>
                </div>
                <p className="mt-4 text-sm font-semibold">Guardrails</p>
                <p className="mt-1 text-xs text-slate-muted">
                  Pre-run hooks attached when this agent version executes.
                </p>
                <div className="mt-3 grid gap-2 sm:grid-cols-3">
                  {(
                    [
                      ["promptInjection", "Prompt injection"],
                      ["piiDetection", "PII detection"],
                      ["openaiModeration", "OpenAI moderation"],
                    ] as const
                  ).map(([key, label]) => (
                    <label
                      key={key}
                      className="flex items-center gap-2 rounded-md border border-line px-2.5 py-2 text-sm"
                    >
                      <input
                        type="checkbox"
                        checked={form.guardrails[key]}
                        onChange={(e) =>
                          update("guardrails", {
                            ...form.guardrails,
                            [key]: e.target.checked,
                          })
                        }
                      />
                      {label}
                    </label>
                  ))}
                </div>
              </div>
            </div>
          </section>

          <ToolAttachmentSection
            tools={form.tools}
            onChange={(tools) => update("tools", tools)}
            toolDefinitions={toolDefinitions}
            credentialNames={credentialNames}
          />

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
    </div>
  );
}
