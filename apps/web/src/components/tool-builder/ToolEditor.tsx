"use client";

import { useRouter } from "next/navigation";
import Link from "next/link";
import { useState } from "react";

import { ToolkitLogo } from "@/components/integrations/ToolkitLogo";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Input, Label, Select, Textarea } from "@/components/ui/Field";
import {
  createToolDefinition,
  type CustomPythonCatalogEntry,
  deleteToolDefinition,
  enumerateToolCapabilities,
  type CredentialSummary,
  type ToolkitCatalogEntry,
  testToolDefinition,
  updateToolDefinition,
} from "@/lib/api/admin";
import type { ToolCapability, ToolDefinition } from "@/lib/api/types";
import { useAgentOsToken } from "@/lib/auth/token";
import { slugifyName } from "@/lib/validation/agent-form";

const EMPTY_SCHEMA = { type: "object", properties: {} };

type EditableTool = Omit<ToolDefinition, "id" | "createdAt" | "updatedAt">;

function customSettingsDefaults(
  entry: CustomPythonCatalogEntry | undefined,
): Record<string, unknown> {
  return Object.fromEntries(
    Object.entries(entry?.settings_schema.properties ?? {})
      .filter(([, property]) => property.default !== undefined)
      .map(([name, property]) => [name, property.default]),
  );
}

export function ToolEditor({
  initial,
  credentials,
  toolkitCatalog,
  customPythonCatalog,
}: {
  initial?: ToolDefinition;
  credentials: CredentialSummary[];
  toolkitCatalog: ToolkitCatalogEntry[];
  customPythonCatalog: CustomPythonCatalogEntry[];
}) {
  const router = useRouter();
  const { getAccessToken } = useAgentOsToken();
  const [form, setForm] = useState<EditableTool>(
    initial ?? {
      name: "",
      slug: "",
      description: "",
      kind: "http",
      httpMethod: "GET",
      baseUrl: "",
      path: "",
      requestSchema: EMPTY_SCHEMA,
      responseDescription: "",
      responseSchema: null,
      headers: {},
      config: {},
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
  const [busy, setBusy] = useState<"save" | "delete" | null>(null);
  const [banner, setBanner] = useState<string | null>(null);
  const [capabilities, setCapabilities] = useState<ToolCapability[]>([]);
  const visibleToolkits = toolkitCatalog.filter((item) => item.exposed);
  const selectedToolkit = visibleToolkits.find(
    (item) => item.key === String(form.config.toolkit ?? "calculator"),
  );
  const selectedCustomTool = customPythonCatalog.find(
    (item) => item.key === String(form.config.custom_tool ?? ""),
  );
  const toolkitCategories = Array.from(
    new Set(visibleToolkits.map((item) => item.category)),
  ).sort();

  function update<K extends keyof EditableTool>(key: K, value: EditableTool[K]) {
    setForm((previous) => ({ ...previous, [key]: value }));
  }

  function updateConfig(key: string, value: unknown) {
    setForm((previous) => ({
      ...previous,
      config: { ...previous.config, [key]: value },
    }));
  }

  async function save() {
    setBanner(null);
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
      (form.kind === "http" && (!form.baseUrl || !form.httpMethod))
    ) {
      setBanner("Complete the required provider fields");
      return;
    }
    setBusy("save");
    try {
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
            : form.config,
        approvalRequired:
          form.kind !== "http" || form.httpMethod === "GET"
            ? form.approvalRequired
            : true,
      };
      const token = await getAccessToken();
      const saved = initial
        ? await updateToolDefinition(token, initial.id, input)
        : await createToolDefinition(token, input);
      setForm({
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
      });
      setBanner("Tool saved");
      if (!initial) router.replace(`/admin/tools/${saved.id}`);
    } catch (error) {
      setBanner(error instanceof Error ? error.message : "Save failed");
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

  return (
    <div className="mx-auto max-w-4xl space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.14em] text-teal">
            Tool builder
          </p>
          <h1 className="font-display text-3xl font-semibold">
            {form.name || "New reusable tool"}
          </h1>
          <div className="mt-2 flex gap-2">
            <Badge tone={form.active ? "success" : "neutral"}>
              {form.active ? "active" : "inactive"}
            </Badge>
            {(form.approvalRequired ||
              (form.kind === "http" && form.httpMethod !== "GET")) && (
              <Badge tone="warning">requires approval</Badge>
            )}
            <Badge tone="info">{form.kind.replace("_", " ")}</Badge>
          </div>
        </div>
        <div className="flex gap-2">
          {initial ? (
            <Button variant="secondary" onClick={remove} disabled={busy !== null}>
              Delete
            </Button>
          ) : null}
          <Button variant="accent" onClick={save} disabled={busy !== null}>
            {busy === "save" ? "Saving…" : "Save tool"}
          </Button>
        </div>
      </div>

      {banner ? (
        <div className="rounded-lg border border-teal/30 bg-teal/10 px-3 py-2 text-sm">
          {banner}
        </div>
      ) : null}

      {form.kind === "openapi" ? (
        <section className="surface-panel rounded-2xl p-5">
          <h2 className="font-display text-lg font-semibold">OpenAPI source</h2>
          <p className="mt-1 text-sm text-slate-muted">
            Import does not expose operations automatically. Save, enumerate,
            review, and explicitly select operation IDs.
          </p>
          <div className="mt-4 space-y-4">
            <div>
              <Label htmlFor="openapi-url">HTTPS document URL (allowlisted host)</Label>
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
              <Label htmlFor="openapi-document">Or pasted OpenAPI JSON/YAML</Label>
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
          </div>
        </section>
      ) : null}

      {form.kind === "python_toolkit" ? (
        <section className="surface-panel rounded-2xl p-5">
          <h2 className="font-display text-lg font-semibold">Allowlisted toolkit</h2>
          <p className="mt-1 text-sm text-slate-muted">
            Toolkits load on the server. Optional packages and tenant secrets never
            reach the browser runtime.
          </p>
          <div className="mt-4 grid gap-4 md:grid-cols-2">
            <div>
              <Label htmlFor="python-toolkit">Server-managed Agno toolkit</Label>
              <Select
                id="python-toolkit"
                value={String(form.config.toolkit ?? "calculator")}
                onChange={(event) => {
                  updateConfig("toolkit", event.target.value);
                  updateConfig("options", {});
                  update("credentialId", null);
                }}
              >
                {toolkitCategories.map((category) => (
                  <optgroup key={category} label={category}>
                    {visibleToolkits
                      .filter((item) => item.category === category)
                      .map((item) => (
                        <option
                          key={item.key}
                          value={item.key}
                          disabled={!item.available}
                        >
                          {item.label}
                          {!item.available
                            ? " — unavailable"
                            : item.credentials.length
                              ? " — needs credential"
                              : ""}
                        </option>
                      ))}
                  </optgroup>
                ))}
              </Select>
            </div>
            {selectedToolkit
              ? Object.entries(selectedToolkit.options).map(([name, option]) => (
                  <div key={name}>
                    <Label htmlFor={`toolkit-option-${name}`}>
                      {name.replaceAll("_", " ")}
                    </Label>
                    <Input
                      id={`toolkit-option-${name}`}
                      type={option.type === "integer" ? "number" : "text"}
                      min={option.minimum}
                      max={option.maximum}
                      value={String(
                        (
                          form.config.options as
                            | Record<string, unknown>
                            | undefined
                        )?.[name] ?? option.default ?? "",
                      )}
                      onChange={(event) =>
                        updateConfig("options", {
                          ...((form.config.options as
                            | Record<string, unknown>
                            | undefined) ?? {}),
                          [name]:
                            option.type === "integer"
                              ? Number(event.target.value)
                              : event.target.value,
                        })
                      }
                    />
                  </div>
                ))
              : null}
          </div>
          {selectedToolkit ? (
            <div className="mt-4 rounded-xl border border-line bg-raised/60 p-3">
              <div className="flex flex-wrap items-center gap-2">
                <ToolkitLogo
                  toolkitKey={selectedToolkit.key}
                  label={selectedToolkit.label}
                  size={24}
                />
                <Badge
                  tone={
                    selectedToolkit.status === "ready"
                      ? "success"
                      : selectedToolkit.status === "needs_credential"
                        ? "warning"
                        : "neutral"
                  }
                >
                  {selectedToolkit.status.replace("_", " ")}
                </Badge>
                {selectedToolkit.side_effects ? (
                  <Badge tone="warning">writes require approval</Badge>
                ) : (
                  <Badge tone="info">read-only by default</Badge>
                )}
                <span className="text-xs text-slate-muted">
                  agno.tools.{selectedToolkit.module}.
                  {selectedToolkit.class_name}
                </span>
              </div>
              <p className="mt-2 text-sm text-slate-muted">
                {selectedToolkit.description}
              </p>
              {selectedToolkit.credentials[0] ? (
                <p className="mt-2 text-xs text-slate-muted">
                  Requires {selectedToolkit.credentials[0].env_var} via a{" "}
                  <Link
                    className="font-medium text-teal hover:underline"
                    href="/admin/credentials"
                  >
                    {selectedToolkit.credentials[0].provider} credential
                  </Link>
                  .
                </p>
              ) : null}
              {selectedToolkit.unavailable_reason ? (
                <p className="mt-2 text-xs text-amber-700">
                  {selectedToolkit.unavailable_reason}
                </p>
              ) : null}
            </div>
          ) : null}
        </section>
      ) : null}

      {form.kind === "custom_python" ? (
        <section className="surface-panel rounded-2xl p-5">
          <h2 className="font-display text-lg font-semibold">
            Source-controlled Python tool
          </h2>
          <p className="mt-1 text-sm text-slate-muted">
            Only tools imported by the backend registry can run. Database values
            cannot supply Python modules, package names, or callable paths.
          </p>
          <div className="mt-4 space-y-4">
            <div>
              <Label htmlFor="custom-python-tool">Registered implementation</Label>
              <Select
                id="custom-python-tool"
                value={String(form.config.custom_tool ?? "")}
                onChange={(event) => {
                  const entry = customPythonCatalog.find(
                    (item) => item.key === event.target.value,
                  );
                  update("config", {
                    custom_tool: event.target.value,
                    settings: customSettingsDefaults(entry),
                    include_tools:
                      entry?.capabilities.map((capability) => capability.name) ?? [],
                    destructive_tools: [],
                  });
                  update("credentialId", null);
                  setCapabilities([]);
                }}
              >
                <option value="">Select registered tool</option>
                {customPythonCatalog.map((entry) => (
                  <option key={entry.key} value={entry.key}>
                    {entry.label}
                  </option>
                ))}
              </Select>
            </div>

            {selectedCustomTool ? (
              <>
                <div className="rounded-xl border border-line bg-raised/60 p-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge tone="success">Source reviewed</Badge>
                    {selectedCustomTool.credential_provider ? (
                      <Badge tone="warning">
                        {selectedCustomTool.credential_provider} credential
                      </Badge>
                    ) : (
                      <Badge tone="info">No credential</Badge>
                    )}
                  </div>
                  <p className="mt-2 text-sm text-slate-muted">
                    {selectedCustomTool.description}
                  </p>
                </div>

                {Object.keys(
                  selectedCustomTool.settings_schema.properties ?? {},
                ).length ? (
                  <div className="grid gap-4 md:grid-cols-2">
                    {Object.entries(
                      selectedCustomTool.settings_schema.properties ?? {},
                    ).map(([name, property]) => {
                      const settings =
                        (form.config.settings as
                          | Record<string, unknown>
                          | undefined) ?? {};
                      return (
                        <div key={name}>
                          <Label
                            htmlFor={`custom-setting-${name}`}
                            hint={
                              selectedCustomTool.settings_schema.required?.includes(
                                name,
                              )
                                ? "Required"
                                : undefined
                            }
                          >
                            {property.title ?? name.replaceAll("_", " ")}
                          </Label>
                          <Input
                            id={`custom-setting-${name}`}
                            type={
                              property.type === "integer" ||
                              property.type === "number"
                                ? "number"
                                : "text"
                            }
                            value={String(
                              settings[name] ?? property.default ?? "",
                            )}
                            onChange={(event) =>
                              updateConfig("settings", {
                                ...settings,
                                [name]:
                                  property.type === "integer" ||
                                  property.type === "number"
                                    ? Number(event.target.value)
                                    : event.target.value,
                              })
                            }
                          />
                        </div>
                      );
                    })}
                  </div>
                ) : null}

                <div>
                  <h3 className="text-sm font-semibold">Exposed functions</h3>
                  <ul className="mt-2 space-y-2">
                    {selectedCustomTool.capabilities.map((capability) => {
                      const included =
                        (form.config.include_tools as string[] | undefined) ?? [];
                      return (
                        <li
                          key={capability.name}
                          className="flex items-start justify-between gap-3 rounded-lg border border-line p-3"
                        >
                          <div>
                            <p className="text-sm font-medium">
                              {capability.name}
                            </p>
                            <p className="text-xs text-slate-muted">
                              {capability.description}
                            </p>
                            {capability.mutating ? (
                              <Badge className="mt-2" tone="warning">
                                Approval always required
                              </Badge>
                            ) : null}
                          </div>
                          <input
                            type="checkbox"
                            checked={included.includes(capability.name)}
                            onChange={(event) =>
                              updateConfig(
                                "include_tools",
                                event.target.checked
                                  ? [...included, capability.name]
                                  : included.filter(
                                      (name) => name !== capability.name,
                                    ),
                              )
                            }
                          />
                        </li>
                      );
                    })}
                  </ul>
                </div>
              </>
            ) : null}
          </div>
        </section>
      ) : null}

      {form.kind === "mcp" ? (
        <section className="surface-panel rounded-2xl border-amber-300 p-5">
          <h2 className="font-display text-lg font-semibold">Remote MCP server</h2>
          <p className="mt-1 text-sm text-slate-muted">
            Remote MCP tools execute outside this platform. Only allowlisted
            public HTTPS endpoints and explicitly selected tools are accepted.
            Stdio commands are platform-managed only and unavailable here.
          </p>
          <div className="mt-4 grid gap-4 md:grid-cols-[0.45fr_1fr]">
            <div>
              <Label htmlFor="mcp-transport">Transport</Label>
              <Select
                id="mcp-transport"
                value={String(form.config.transport ?? "streamable-http")}
                onChange={(event) => updateConfig("transport", event.target.value)}
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
                onChange={(event) => updateConfig("url", event.target.value)}
                placeholder="https://mcp.example.com/mcp"
              />
            </div>
          </div>
        </section>
      ) : null}

      {form.kind !== "http" && form.kind !== "custom_python" ? (
        <section className="surface-panel rounded-2xl p-5">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 className="font-display text-lg font-semibold">Capabilities</h2>
              <p className="text-sm text-slate-muted">
                Enumerate remote capabilities, then select only reviewed tools.
              </p>
            </div>
            <div className="flex gap-2">
              <Button
                variant="secondary"
                onClick={() => inspectProvider(false)}
                disabled={!initial}
              >
                Enumerate
              </Button>
              <Button
                variant="secondary"
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
                  className="flex items-start justify-between gap-3 rounded-lg border border-line p-3"
                >
                  <div>
                    <p className="text-sm font-medium">{capability.name}</p>
                    <p className="text-xs text-slate-muted">{capability.description}</p>
                    {capability.approvalRequired ? (
                      <Badge tone="warning">minimum approval enforced</Badge>
                    ) : null}
                  </div>
                  <input
                    type="checkbox"
                    checked={selected}
                    onChange={(event) => {
                      const key =
                        form.kind === "openapi" ? "allowed_operations" : "include_tools";
                      const current = (form.config[key] as string[] | undefined) ?? [];
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
      ) : null}

      <section className="surface-panel rounded-2xl p-5">
        <h2 className="font-display text-lg font-semibold">Definition</h2>
        <div className="mt-4 grid gap-4 md:grid-cols-2">
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
            <Label htmlFor="tool-slug">Callable slug</Label>
            <Input
              id="tool-slug"
              value={form.slug}
              disabled={Boolean(initial)}
              onChange={(event) => update("slug", event.target.value)}
            />
          </div>
          <div className="md:col-span-2">
            <Label htmlFor="tool-description">Description shown to the model</Label>
            <Input
              id="tool-description"
              value={form.description}
              onChange={(event) => update("description", event.target.value)}
            />
          </div>
        </div>
      </section>

      <section className="surface-panel rounded-2xl p-5">
        <h2 className="font-display text-lg font-semibold">Provider</h2>
        <p className="mt-1 text-sm text-slate-muted">
          Choose a constrained provider. Python modules, uploaded code, package
          installs, and tenant-supplied MCP stdio commands are never accepted.
        </p>
        <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
          {[
            ["http", "HTTP Request", "One reviewed request"],
            ["openapi", "OpenAPI", "Selected spec operations"],
            ["python_toolkit", "Python Toolkit", "Server allowlist only"],
            ["custom_python", "Custom Python", "Source-controlled registry"],
            ["mcp", "MCP Server", "Remote HTTP transport"],
          ].map(([kind, label, description]) => (
            <button
              key={kind}
              type="button"
              disabled={Boolean(initial)}
              onClick={() => {
                update("kind", kind as EditableTool["kind"]);
                update(
                  "config",
                  kind === "python_toolkit"
                    ? { toolkit: "calculator", options: {}, include_tools: [] }
                    : kind === "custom_python"
                      ? {
                          custom_tool: customPythonCatalog[0]?.key ?? "",
                          settings: customSettingsDefaults(customPythonCatalog[0]),
                          include_tools:
                            customPythonCatalog[0]?.capabilities.map(
                              (capability) => capability.name,
                            ) ?? [],
                          destructive_tools: [],
                        }
                    : kind === "mcp"
                      ? {
                          transport: "streamable-http",
                          url: "",
                          include_tools: [],
                          exclude_tools: [],
                        }
                      : kind === "openapi"
                        ? { source_url: null, document: null, allowed_operations: [] }
                        : {},
                );
                setCapabilities([]);
              }}
              className={`rounded-xl border p-3 text-left ${
                form.kind === kind ? "border-teal bg-teal/10" : "border-line bg-raised/70"
              } disabled:cursor-not-allowed`}
            >
              <span className="block text-sm font-semibold">{label}</span>
              <span className="text-xs text-slate-muted">{description}</span>
            </button>
          ))}
        </div>
      </section>

      {form.kind === "http" ? (
      <section className="surface-panel rounded-2xl p-5">
        <h2 className="font-display text-lg font-semibold">HTTP request</h2>
        <div className="mt-4 grid gap-4 md:grid-cols-[0.4fr_1fr]">
          <div>
            <Label htmlFor="tool-method">Method</Label>
            <Select
              id="tool-method"
              value={form.httpMethod ?? "GET"}
              onChange={(event) =>
                update(
                  "httpMethod",
                  event.target.value as NonNullable<EditableTool["httpMethod"]>,
                )
              }
            >
              {["GET", "POST", "PUT", "PATCH", "DELETE"].map((method) => (
                <option key={method}>{method}</option>
              ))}
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
            <Label htmlFor="tool-schema">Request parameter JSON schema</Label>
            <Textarea
              id="tool-schema"
              className="min-h-56 font-mono text-[13px]"
              value={schemaText}
              onChange={(event) => setSchemaText(event.target.value)}
            />
          </div>
        </div>
      </section>
      ) : null}

      <section className="surface-panel rounded-2xl p-5">
        <h2 className="font-display text-lg font-semibold">Security and lifecycle</h2>
        <div className="mt-4 grid gap-4 md:grid-cols-2">
          <div>
            <Label htmlFor="tool-credential">Server-side credential</Label>
            <Select
              id="tool-credential"
              value={form.credentialId ?? ""}
              disabled={
                (form.kind === "python_toolkit" &&
                  !selectedToolkit?.credentials.length) ||
                (form.kind === "custom_python" &&
                  !selectedCustomTool?.credential_provider)
              }
              onChange={(event) => update("credentialId", event.target.value || null)}
            >
              <option value="">No credential</option>
              {credentials
                .filter((credential) =>
                  form.kind === "python_toolkit"
                    ? credential.provider ===
                      selectedToolkit?.credentials[0]?.provider
                    : form.kind === "custom_python"
                      ? credential.provider ===
                        selectedCustomTool?.credential_provider
                    : credential.provider === "rest_api",
                )
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
          <div className="space-y-3 pt-6">
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={
                  form.approvalRequired ||
                  (form.kind === "http" && form.httpMethod !== "GET")
                }
                disabled={form.kind === "http" && form.httpMethod !== "GET"}
                onChange={(event) => update("approvalRequired", event.target.checked)}
              />
              Require approval
            </label>
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
    </div>
  );
}
