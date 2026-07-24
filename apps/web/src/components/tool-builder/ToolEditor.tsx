"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { BackLink } from "@/components/ui/BackLink";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { EditorActions } from "@/components/ui/EditorActions";
import { Input, Label, Select, Textarea } from "@/components/ui/Field";
import { SaveIcon, TrashIcon } from "@/components/ui/icons";
import {
  createToolDefinition,
  deleteToolDefinition,
  enumerateToolCapabilities,
  type CredentialSummary,
  publishTenantPythonTool,
  testToolDefinition,
  updateToolDefinition,
  validateTenantPythonSource,
  validateToolDefinition,
} from "@/lib/api/admin";
import type { ToolCapability, ToolDefinition } from "@/lib/api/types";
import { useAgentOsToken } from "@/lib/auth/token";
import { cn } from "@/lib/utils";
import { slugifyName } from "@/lib/validation/agent-form";

const EMPTY_SCHEMA = { type: "object", properties: {} };

type ToolKind = ToolDefinition["kind"];

type KindOption = {
  kind: "http" | "openapi" | "tenant_python" | "mcp";
  label: string;
};

/** Create options for the Tools UI. Editable Python replaces toolkit/custom Python. */
const KIND_TABS: KindOption[] = [
  { kind: "http", label: "HTTP Request" },
  { kind: "openapi", label: "OpenAPI" },
  { kind: "tenant_python", label: "Editable Python" },
  { kind: "mcp", label: "MCP Server" },
];

/** Removed from create/edit; existing definitions stay viewable. */
const DEPRECATED_KINDS = new Set<ToolKind>(["python_toolkit", "custom_python"]);

type EditableTool = Omit<ToolDefinition, "id" | "createdAt" | "updatedAt">;

type TenantCapability = {
  name: string;
  description: string;
  mutating: boolean;
  input_schema: Record<string, unknown>;
};

function tenantPythonDefaults(): Record<string, unknown> {
  return {
    source_code: "",
    dependencies: [],
    capabilities: [],
    settings: {},
    template: null,
    version_status: "draft",
  };
}

function applySavedForm(saved: ToolDefinition): EditableTool {
  return {
    name: saved.name,
    slug: saved.slug,
    description: saved.description,
    kind: saved.kind,
    httpMethod: saved.httpMethod,
    baseUrl: saved.baseUrl,
    path: saved.path,
    requestSchema: saved.requestSchema,
    responseDescription: saved.responseDescription,
    responseSchema: saved.responseSchema,
    headers: saved.headers,
    config: saved.config,
    credentialId: saved.credentialId,
    approvalRequired: saved.approvalRequired,
    active: saved.active,
    connectionStatus: saved.connectionStatus,
    lastValidatedAt: saved.lastValidatedAt,
    lastValidationError: saved.lastValidationError,
    publishedVersionId: saved.publishedVersionId ?? null,
  };
}

function capabilitiesToConfig(capabilities: ToolCapability[]): TenantCapability[] {
  return capabilities.map((capability) => ({
    name: capability.name,
    description: capability.description,
    mutating: capability.approvalRequired,
    input_schema: capability.inputSchema,
  }));
}

function emptyConfigForKind(kind: KindOption["kind"]): Record<string, unknown> {
  if (kind === "tenant_python") return tenantPythonDefaults();
  if (kind === "mcp") {
    return {
      transport: "streamable-http",
      url: "",
      include_tools: [],
      exclude_tools: [],
    };
  }
  if (kind === "openapi") {
    return {
      source_url: null,
      document: null,
      allowed_operations: [],
    };
  }
  return {};
}

function resolveCreateKind(
  defaultKind?: ToolKind,
): KindOption["kind"] {
  if (
    defaultKind === "http" ||
    defaultKind === "openapi" ||
    defaultKind === "tenant_python" ||
    defaultKind === "mcp"
  ) {
    return defaultKind;
  }
  return "http";
}

export function ToolEditor({
  initial,
  credentials,
  defaultKind,
}: {
  initial?: ToolDefinition;
  credentials: CredentialSummary[];
  /** Prefill kind when creating from a Tools list tab. */
  defaultKind?: ToolKind;
}) {
  const router = useRouter();
  const { getAccessToken } = useAgentOsToken();
  const createKind = resolveCreateKind(defaultKind);
  const [form, setForm] = useState<EditableTool>(
    initial ?? {
      name: "",
      slug: "",
      description: "",
      kind: createKind,
      httpMethod: "GET",
      baseUrl: "",
      path: "",
      requestSchema: EMPTY_SCHEMA,
      responseDescription: "",
      responseSchema: null,
      headers: {},
      config: emptyConfigForKind(createKind),
      credentialId: null,
      approvalRequired: false,
      active: true,
      connectionStatus: "unvalidated",
      lastValidatedAt: null,
      lastValidationError: null,
    },
  );
  const [schemaText, setSchemaText] = useState(
    JSON.stringify(form.requestSchema, null, 2),
  );
  const [busy, setBusy] = useState<"save" | "delete" | "validate" | "publish" | null>(
    null,
  );
  const [banner, setBanner] = useState<string | null>(null);
  const [capabilities, setCapabilities] = useState<ToolCapability[]>([]);
  const isDeprecatedKind = DEPRECATED_KINDS.has(form.kind);
  const tenantCapabilities =
    (form.config.capabilities as TenantCapability[] | undefined) ?? [];
  const versionStatus = String(form.config.version_status ?? "draft");
  const kindLocked = Boolean(initial) || isDeprecatedKind;

  function update<K extends keyof EditableTool>(key: K, value: EditableTool[K]) {
    setForm((previous) => ({ ...previous, [key]: value }));
  }

  function updateConfig(key: string, value: unknown) {
    setForm((previous) => ({
      ...previous,
      config: { ...previous.config, [key]: value },
    }));
  }

  function selectKind(kind: KindOption["kind"]) {
    if (kindLocked) return;
    update("kind", kind);
    update("config", emptyConfigForKind(kind));
    setCapabilities([]);
  }

  async function saveLifecycle(nextActive: boolean) {
    if (!initial || !isDeprecatedKind) return;
    setBanner(null);
    setBusy("save");
    try {
      const token = await getAccessToken();
      const saved = await updateToolDefinition(token, initial.id, {
        ...form,
        active: nextActive,
      });
      setForm(applySavedForm(saved));
      setBanner(nextActive ? "Tool reactivated" : "Tool deactivated");
    } catch (error) {
      setBanner(error instanceof Error ? error.message : "Update failed");
    } finally {
      setBusy(null);
    }
  }

  function buildTenantPythonConfig(): Record<string, unknown> {
    return {
      source_code: String(form.config.source_code ?? ""),
      // Backend AST-discovers async defs when capabilities is empty.
      capabilities: [] as TenantCapability[],
      dependencies:
        (form.config.dependencies as Array<{ name: string; version: string }> | undefined) ??
        [],
      settings: (form.config.settings as Record<string, unknown> | undefined) ?? {},
      template: form.config.template ?? null,
      version_status: form.config.version_status ?? "draft",
    };
  }

  async function save() {
    setBanner(null);
    if (isDeprecatedKind) {
      setBanner(
        "This tool kind is no longer available for editing; recreate as Editable Python or HTTP.",
      );
      return;
    }
    let requestSchema = EMPTY_SCHEMA;
    if (form.kind === "http") {
      try {
        requestSchema = JSON.parse(schemaText) as typeof EMPTY_SCHEMA;
        if (
          requestSchema.type !== "object" ||
          typeof requestSchema.properties !== "object" ||
          requestSchema.properties === null
        ) {
          throw new Error("Root schema must be an object with properties");
        }
      } catch (error) {
        setBanner(error instanceof Error ? error.message : "Invalid JSON schema");
        return;
      }
    }
    if (
      !form.name.trim() ||
      !form.slug ||
      (form.kind === "http" && (!form.baseUrl || !form.httpMethod)) ||
      (form.kind === "tenant_python" && !String(form.config.source_code ?? "").trim())
    ) {
      setBanner("Complete the required provider fields");
      return;
    }
    setBusy("save");
    try {
      const tenantConfig =
        form.kind === "tenant_python" ? buildTenantPythonConfig() : form.config;
      const input = {
        ...form,
        requestSchema,
        config:
          form.kind === "http"
            ? {
                base_url: form.baseUrl,
                method: form.httpMethod,
                path: form.path,
                request_schema: requestSchema,
                response_description: form.responseDescription || null,
                response_schema: form.responseSchema,
                headers: form.headers,
                credential_header: String(
                  form.config.credential_header ?? "Authorization",
                ),
                credential_prefix: String(
                  form.config.credential_prefix ?? "Bearer ",
                ),
                timeout_seconds: Number(form.config.timeout_seconds ?? 10),
              }
            : tenantConfig,
        approvalRequired:
          form.kind === "tenant_python"
            ? form.approvalRequired
            : form.kind !== "http" || form.httpMethod === "GET"
              ? form.approvalRequired
              : true,
      };
      const token = await getAccessToken();
      const saved = initial
        ? await updateToolDefinition(token, initial.id, input)
        : await createToolDefinition(token, input);
      setForm(applySavedForm(saved));
      if (saved.kind === "tenant_python") {
        const derived =
          (saved.config.capabilities as TenantCapability[] | undefined) ?? [];
        setCapabilities(
          derived.map((capability) => ({
            name: capability.name,
            description: capability.description,
            approvalRequired: capability.mutating,
            inputSchema: capability.input_schema,
          })),
        );
      }
      setBanner(
        saved.kind === "tenant_python"
          ? `Tool saved — ${
              ((saved.config.capabilities as TenantCapability[] | undefined) ?? []).length
            } capabilities derived from source`
          : "Tool saved",
      );
      if (!initial) router.replace(`/admin/tools/${saved.id}`);
    } catch (error) {
      setBanner(error instanceof Error ? error.message : "Save failed");
    } finally {
      setBusy(null);
    }
  }

  async function validateSource() {
    if (!initial) {
      setBanner("Save the definition before validating.");
      return;
    }
    setBusy("validate");
    setBanner(null);
    try {
      const token = await getAccessToken();
      if (form.kind === "tenant_python") {
        // Persist latest source first so validate-source inspects current draft.
        const saved = await updateToolDefinition(token, initial.id, {
          ...form,
          config: buildTenantPythonConfig(),
        });
        const result = await validateTenantPythonSource(token, initial.id);
        const mapped = capabilitiesToConfig(result.capabilities);
        setCapabilities(result.capabilities);
        setForm({
          ...applySavedForm(saved),
          approvalRequired:
            saved.approvalRequired ||
            result.capabilities.some((item) => item.approvalRequired),
          config: {
            ...saved.config,
            capabilities: mapped,
            version_status: "validated",
          },
        });
        setBanner(
          `${result.message} — synced ${result.capabilities.length} capabilities from source`,
        );
      } else {
        const result = await validateToolDefinition(token, form);
        setCapabilities(result.capabilities);
        setBanner(result.message);
      }
    } catch (error) {
      setBanner(error instanceof Error ? error.message : "Validation failed");
    } finally {
      setBusy(null);
    }
  }

  async function publishSource() {
    if (!initial || form.kind !== "tenant_python") return;
    setBusy("publish");
    setBanner(null);
    try {
      const token = await getAccessToken();
      const saved = await publishTenantPythonTool(token, initial.id);
      setForm((previous) => ({
        ...previous,
        config: saved.config,
        approvalRequired: saved.approvalRequired,
        publishedVersionId: saved.publishedVersionId ?? null,
      }));
      setBanner("Published — agents can now attach this tool");
    } catch (error) {
      setBanner(error instanceof Error ? error.message : "Publish failed");
    } finally {
      setBusy(null);
    }
  }

  async function inspectProvider(test: boolean) {
    if (!initial) {
      setBanner("Save the definition before connecting to a remote provider.");
      return;
    }
    setBanner(null);
    try {
      const token = await getAccessToken();
      const result = test
        ? await testToolDefinition(token, initial.id)
        : await enumerateToolCapabilities(token, initial.id);
      setCapabilities(result.capabilities);
      setBanner(result.message);
    } catch (error) {
      setBanner(error instanceof Error ? error.message : "Provider check failed");
    }
  }

  async function remove() {
    if (!initial || !window.confirm("Delete this tool definition?")) return;
    setBusy("delete");
    try {
      await deleteToolDefinition(await getAccessToken(), initial.id);
      router.push("/admin/tools");
      router.refresh();
    } catch (error) {
      setBanner(error instanceof Error ? error.message : "Delete failed");
      setBusy(null);
    }
  }

  const discoveredNames =
    form.kind === "tenant_python"
      ? tenantCapabilities.map((item) => item.name).filter(Boolean)
      : capabilities.map((item) => item.name);

  return (
    <div className="mx-auto max-w-4xl space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.14em] text-teal">
            Tool builder
          </p>
          <div className="flex min-w-0 items-center gap-1.5">
            <BackLink href="/admin/tools" label="Back to tools" />
            <h1 className="font-display text-3xl font-semibold">
              {form.name || "New reusable tool"}
            </h1>
          </div>
          <div className="mt-2 flex flex-wrap gap-2">
            <Badge tone={form.active ? "success" : "neutral"}>
              {form.active ? "active" : "inactive"}
            </Badge>
            {(form.approvalRequired ||
              (form.kind === "http" && form.httpMethod !== "GET") ||
              (form.kind === "tenant_python" &&
                tenantCapabilities.some((item) => item.mutating))) && (
              <Badge tone="warning">requires approval</Badge>
            )}
            <Badge tone="info">{form.kind.replace("_", " ")}</Badge>
            {form.kind === "tenant_python" ? (
              <Badge tone={versionStatus === "published" ? "success" : "neutral"}>
                {versionStatus}
              </Badge>
            ) : null}
          </div>
        </div>
        <EditorActions>
          {initial ? (
            <Button
              variant="secondary"
              size="sm"
              onClick={remove}
              disabled={busy !== null}
            >
              <TrashIcon />
              {busy === "delete" ? "Deleting…" : "Delete"}
            </Button>
          ) : null}
          {form.kind === "tenant_python" && initial ? (
            <>
              <Button
                variant="secondary"
                size="sm"
                onClick={validateSource}
                disabled={busy !== null}
              >
                {busy === "validate" ? "Validating…" : "Validate"}
              </Button>
              <Button
                variant="secondary"
                size="sm"
                onClick={publishSource}
                disabled={busy !== null}
              >
                {busy === "publish" ? "Publishing…" : "Publish"}
              </Button>
            </>
          ) : null}
          {isDeprecatedKind ? (
            <Button
              variant="accent"
              size="sm"
              onClick={() => void saveLifecycle(!form.active)}
              disabled={busy !== null}
            >
              <SaveIcon />
              {busy === "save"
                ? "Saving…"
                : form.active
                  ? "Deactivate"
                  : "Reactivate"}
            </Button>
          ) : (
            <Button
              variant="accent"
              size="sm"
              onClick={save}
              disabled={busy !== null}
            >
              <SaveIcon />
              {busy === "save" ? "Saving…" : "Save tool"}
            </Button>
          )}
        </EditorActions>
      </div>

      {banner ? (
        <div
          className={
            /failed|error|invalid|no longer available|required/i.test(banner)
              ? "max-h-28 overflow-y-auto break-words rounded-lg border border-rose/40 bg-rose/10 px-3 py-2 text-sm text-rose"
              : "max-h-28 overflow-y-auto break-words rounded-lg border border-teal/30 bg-teal/10 px-3 py-2 text-sm"
          }
          role="status"
        >
          {banner}
        </div>
      ) : null}

      {isDeprecatedKind ? (
        <section className="surface-panel rounded-2xl border-amber-300 p-5">
          <h2 className="font-display text-lg font-semibold">
            Kind no longer available
          </h2>
          <p className="mt-2 text-sm text-slate-muted">
            This tool kind is no longer available for editing; recreate as Editable
            Python or HTTP. You can deactivate this definition or delete it.
          </p>
          <p className="mt-3 mono-cell text-xs text-slate-muted">
            kind: {form.kind}
            {form.kind === "python_toolkit" && form.config.toolkit
              ? ` · toolkit: ${String(form.config.toolkit)}`
              : null}
            {form.kind === "custom_python" && form.config.custom_tool
              ? ` · registry: ${String(form.config.custom_tool)}`
              : null}
          </p>
        </section>
      ) : null}

      {!isDeprecatedKind ? (
        <>
          <div
            role="tablist"
            aria-label="Tool type"
            className="flex flex-wrap gap-1 border-b border-line"
          >
            {KIND_TABS.map(({ kind, label }) => {
              const selected = form.kind === kind;
              return (
                <button
                  key={kind}
                  type="button"
                  role="tab"
                  aria-selected={selected}
                  disabled={kindLocked && !selected}
                  onClick={() => selectKind(kind)}
                  className={cn(
                    "-mb-px border-b-2 px-3 py-2 text-sm font-medium transition",
                    selected
                      ? "border-teal text-ink"
                      : "border-transparent text-slate-muted hover:text-ink",
                    kindLocked && !selected && "cursor-not-allowed opacity-40",
                  )}
                >
                  {label}
                </button>
              );
            })}
          </div>

          <section
            className="rounded-xl border border-line bg-raised/40 p-4"
            role="tabpanel"
          >
            <div className="space-y-4">
              <div className="grid gap-4 md:grid-cols-2">
                <div>
                  <Label htmlFor="tool-name">Name</Label>
                  <Input
                    id="tool-name"
                    value={form.name}
                    onChange={(event) => {
                      const name = event.target.value;
                      update("name", name);
                      if (!initial) update("slug", slugifyName(name));
                    }}
                  />
                </div>
                <div>
                  <Label htmlFor="tool-slug">Slug</Label>
                  <Input
                    id="tool-slug"
                    value={form.slug}
                    disabled={Boolean(initial)}
                    onChange={(event) => update("slug", event.target.value)}
                  />
                </div>
              </div>

              <div>
                <Label htmlFor="tool-description">Description</Label>
                <Input
                  id="tool-description"
                  value={form.description ?? ""}
                  onChange={(event) => update("description", event.target.value)}
                  placeholder="Optional — shown to the model"
                />
              </div>

              {form.kind === "http" ? (
                <div className="grid gap-4 md:grid-cols-[0.4fr_1fr]">
                  <div>
                    <Label htmlFor="tool-method">Method</Label>
                    <Select
                      id="tool-method"
                      value={form.httpMethod ?? "GET"}
                      onChange={(event) =>
                        update(
                          "httpMethod",
                          event.target.value as NonNullable<
                            EditableTool["httpMethod"]
                          >,
                        )
                      }
                    >
                      {["GET", "POST", "PUT", "PATCH", "DELETE"].map(
                        (method) => (
                          <option key={method}>{method}</option>
                        ),
                      )}
                    </Select>
                  </div>
                  <div>
                    <Label htmlFor="tool-url">Allowlisted HTTPS base URL</Label>
                    <Input
                      id="tool-url"
                      placeholder="https://api.example.com/v1"
                      value={form.baseUrl ?? ""}
                      onChange={(event) => update("baseUrl", event.target.value)}
                    />
                  </div>
                  <div className="md:col-span-2">
                    <Label htmlFor="tool-path" hint="Use {parameter} placeholders">
                      Path
                    </Label>
                    <Input
                      id="tool-path"
                      placeholder="/customers/{customer_id}"
                      value={form.path ?? ""}
                      onChange={(event) => update("path", event.target.value)}
                    />
                  </div>
                  <div className="md:col-span-2">
                    <Label htmlFor="tool-schema">
                      Request parameter JSON schema
                    </Label>
                    <Textarea
                      id="tool-schema"
                      className="min-h-56 font-mono text-[13px]"
                      value={schemaText}
                      onChange={(event) => setSchemaText(event.target.value)}
                    />
                  </div>
                </div>
              ) : null}

              {form.kind === "openapi" ? (
                <>
                  <p className="text-sm text-slate-muted">
                    Import does not expose operations automatically. Save,
                    enumerate, review, and explicitly select operation IDs.
                  </p>
                  <div>
                    <Label htmlFor="openapi-url">
                      HTTPS document URL (allowlisted host)
                    </Label>
                    <Input
                      id="openapi-url"
                      value={String(form.config.source_url ?? "")}
                      onChange={(event) => {
                        updateConfig("source_url", event.target.value || null);
                        updateConfig("document", null);
                      }}
                      placeholder="https://api.example.com/openapi.json"
                    />
                  </div>
                  <div>
                    <Label htmlFor="openapi-document">
                      Or pasted OpenAPI JSON/YAML
                    </Label>
                    <Textarea
                      id="openapi-document"
                      className="min-h-56 font-mono text-[13px]"
                      value={
                        typeof form.config.document === "string"
                          ? form.config.document
                          : form.config.document
                            ? JSON.stringify(form.config.document, null, 2)
                            : ""
                      }
                      onChange={(event) => {
                        updateConfig("document", event.target.value || null);
                        updateConfig("source_url", null);
                      }}
                    />
                  </div>
                </>
              ) : null}

              {form.kind === "tenant_python" ? (
                <>
                  <div>
                    <Label htmlFor="tool-credential">Credential</Label>
                    <Select
                      id="tool-credential"
                      value={form.credentialId ?? ""}
                      onChange={(event) =>
                        update("credentialId", event.target.value || null)
                      }
                    >
                      <option value="">No credential</option>
                      {credentials
                        .filter(
                          (credential) => credential.provider === "rest_api",
                        )
                        .map((credential) => (
                          <option key={credential.id} value={credential.id}>
                            {credential.name}
                          </option>
                        ))}
                    </Select>
                    <p className="mt-1 text-xs text-slate-muted">
                      Optional JSON credential keys are available in tool code at
                      runtime. Secret values are never returned to this page.
                    </p>
                  </div>

                  <div>
                    <Label htmlFor="tenant-python-source">Source</Label>
                    <Textarea
                      id="tenant-python-source"
                      className="min-h-[28rem] font-mono text-[13px] leading-relaxed"
                      value={String(form.config.source_code ?? "")}
                      onChange={(event) => {
                        updateConfig("source_code", event.target.value);
                        updateConfig("version_status", "draft");
                      }}
                      placeholder="async def …"
                    />
                    <p className="mt-1 text-xs text-slate-muted">
                      Capabilities are derived from{" "}
                      <code className="font-mono">async def</code> names on save
                      and validate — no manual list required.
                    </p>
                    {discoveredNames.length > 0 ? (
                      <p className="mt-2 text-xs text-slate-muted">
                        Discovered:{" "}
                        <span className="font-mono text-ink">
                          {discoveredNames.join(", ")}
                        </span>
                      </p>
                    ) : null}
                  </div>
                </>
              ) : null}

              {form.kind === "mcp" ? (
                <>
                  <p className="rounded-lg border border-amber-300/60 bg-amber-50/50 px-3 py-2 text-sm text-slate-muted">
                    Remote MCP tools execute outside this platform. Only
                    allowlisted public HTTPS endpoints and explicitly selected
                    tools are accepted.
                  </p>
                  <div className="grid gap-4 md:grid-cols-[0.45fr_1fr]">
                    <div>
                      <Label htmlFor="mcp-transport">Transport</Label>
                      <Select
                        id="mcp-transport"
                        value={String(
                          form.config.transport ?? "streamable-http",
                        )}
                        onChange={(event) =>
                          updateConfig("transport", event.target.value)
                        }
                      >
                        <option value="streamable-http">Streamable HTTP</option>
                        <option value="sse">SSE (legacy)</option>
                      </Select>
                    </div>
                    <div>
                      <Label htmlFor="mcp-url">Allowlisted HTTPS endpoint</Label>
                      <Input
                        id="mcp-url"
                        value={String(form.config.url ?? "")}
                        onChange={(event) =>
                          updateConfig("url", event.target.value)
                        }
                        placeholder="https://mcp.example.com/mcp"
                      />
                    </div>
                  </div>
                </>
              ) : null}
            </div>
          </section>

          {(form.kind === "openapi" || form.kind === "mcp") && (
            <section className="rounded-xl border border-line bg-raised/40 p-4">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <h2 className="text-sm font-semibold">Capabilities</h2>
                  <p className="text-sm text-slate-muted">
                    Enumerate remote capabilities, then select only reviewed tools.
                  </p>
                </div>
                <div className="flex gap-2">
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={() => inspectProvider(false)}
                    disabled={!initial}
                  >
                    Enumerate
                  </Button>
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={() => inspectProvider(true)}
                    disabled={!initial}
                  >
                    Test connection
                  </Button>
                </div>
              </div>
              <ul className="mt-4 space-y-2">
                {capabilities.map((capability) => {
                  const selected = (
                    (form.config.allowed_operations as string[] | undefined) ??
                    (form.config.include_tools as string[] | undefined) ??
                    []
                  ).includes(capability.name);
                  return (
                    <li
                      key={capability.name}
                      className="flex items-start justify-between gap-3 rounded-lg border border-line px-3 py-2"
                    >
                      <div>
                        <p className="text-sm font-medium">{capability.name}</p>
                        <p className="text-xs text-slate-muted">
                          {capability.description}
                        </p>
                        {capability.approvalRequired ? (
                          <Badge tone="warning">minimum approval enforced</Badge>
                        ) : null}
                      </div>
                      <input
                        type="checkbox"
                        checked={selected}
                        onChange={(event) => {
                          const key =
                            form.kind === "openapi"
                              ? "allowed_operations"
                              : "include_tools";
                          const current =
                            (form.config[key] as string[] | undefined) ?? [];
                          updateConfig(
                            key,
                            event.target.checked
                              ? [...current, capability.name]
                              : current.filter((name) => name !== capability.name),
                          );
                        }}
                      />
                    </li>
                  );
                })}
              </ul>
            </section>
          )}

          <section className="rounded-xl border border-line bg-raised/40 p-4">
            <h2 className="text-sm font-semibold">
              {form.kind === "tenant_python" ? "Lifecycle" : "Security and lifecycle"}
            </h2>
            <div
              className={cn(
                "mt-4 grid gap-4",
                form.kind === "tenant_python" ? "" : "md:grid-cols-2",
              )}
            >
              {form.kind !== "tenant_python" ? (
                <div>
                  <Label htmlFor="tool-credential-shared">Server-side credential</Label>
                  <Select
                    id="tool-credential-shared"
                    value={form.credentialId ?? ""}
                    onChange={(event) =>
                      update("credentialId", event.target.value || null)
                    }
                  >
                    <option value="">No credential</option>
                    {credentials
                      .filter((credential) => credential.provider === "rest_api")
                      .map((credential) => (
                        <option key={credential.id} value={credential.id}>
                          {credential.name}
                        </option>
                      ))}
                  </Select>
                  <p className="mt-1 text-xs text-slate-muted">
                    Secret values are never returned to this page.
                  </p>
                </div>
              ) : null}
              <div
                className={cn(
                  "space-y-3",
                  form.kind !== "tenant_python" && "pt-6",
                )}
              >
                {form.kind !== "tenant_python" ? (
                  <label className="flex items-center gap-2 text-sm">
                    <input
                      type="checkbox"
                      checked={
                        form.approvalRequired ||
                        (form.kind === "http" && form.httpMethod !== "GET")
                      }
                      disabled={form.kind === "http" && form.httpMethod !== "GET"}
                      onChange={(event) =>
                        update("approvalRequired", event.target.checked)
                      }
                    />
                    Require approval
                  </label>
                ) : null}
                <label className="flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={form.active}
                    onChange={(event) => update("active", event.target.checked)}
                  />
                  Active and attachable
                </label>
              </div>
            </div>
          </section>
        </>
      ) : null}
    </div>
  );
}
