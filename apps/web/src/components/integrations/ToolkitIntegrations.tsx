"use client";

import { Link } from "@/i18n/navigation";
import { useMemo, useState } from "react";

import { ToolkitLogo } from "@/components/integrations/ToolkitLogo";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Input, Label, Select } from "@/components/ui/Field";
import { SearchableSelect } from "@/components/ui/SearchableSelect";
import {
  createCredential,
  createToolDefinition,
  type CredentialSummary,
  type ToolkitCatalogEntry,
} from "@/lib/api/admin";
import type { ToolDefinition } from "@/lib/api/types";
import { useAgentOsToken } from "@/lib/auth/token";
import { cn } from "@/lib/utils";
import { slugifyName } from "@/lib/validation/agent-form";

type Filter = "all" | "configured" | "needs_setup" | "unavailable";

function optionDefaults(toolkit: ToolkitCatalogEntry): Record<string, unknown> {
  return Object.fromEntries(
    Object.entries(toolkit.options)
      .filter(([, option]) => option.default !== undefined)
      .map(([name, option]) => [name, option.default]),
  );
}

function isMissingPackage(toolkit: ToolkitCatalogEntry): boolean {
  const reason = `${toolkit.unavailable_reason ?? ""} ${toolkit.install_hint ?? ""}`;
  return Boolean(
    toolkit.install_hint &&
      /pip install|not installed|no module named/i.test(reason),
  );
}

function unavailableTitle(toolkit: ToolkitCatalogEntry): string {
  if (toolkit.disabled_reason || toolkit.status === "blocked") {
    return "Unavailable by policy";
  }
  if (isMissingPackage(toolkit)) {
    return "Server package required";
  }
  if (
    /credential|oauth|option|configuration|configure|multi-value/i.test(
      toolkit.unavailable_reason ?? "",
    )
  ) {
    return "Configuration required";
  }
  return "Toolkit unavailable";
}

function unavailableDetail(toolkit: ToolkitCatalogEntry): string {
  return (
    toolkit.unavailable_reason ||
    toolkit.disabled_reason ||
    toolkit.install_hint ||
    "This toolkit is not available in the backend image."
  );
}

export function ToolkitIntegrations({
  catalog,
  initialCredentials,
  initialTools,
}: {
  catalog: ToolkitCatalogEntry[];
  initialCredentials: CredentialSummary[];
  initialTools: ToolDefinition[];
}) {
  const visible = useMemo(() => catalog.filter((item) => item.exposed), [catalog]);
  const [credentials, setCredentials] = useState(initialCredentials);
  const [tools, setTools] = useState(initialTools);
  const [selectedKey, setSelectedKey] = useState(visible[0]?.key ?? "");
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const [options, setOptions] = useState<Record<string, unknown>>(
    visible[0] ? optionDefaults(visible[0]) : {},
  );
  const [credentialId, setCredentialId] = useState(
    initialCredentials.find(
      (credential) =>
        credential.provider === visible[0]?.credentials[0]?.provider,
    )?.id ?? "",
  );
  const [secretLabel, setSecretLabel] = useState("");
  const [secretValue, setSecretValue] = useState("");
  const [busy, setBusy] = useState<"credential" | "tool" | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const { getAccessToken } = useAgentOsToken();

  const selected = visible.find((item) => item.key === selectedKey) ?? null;
  const configuredKeys = new Set(
    tools
      .filter((tool) => tool.kind === "python_toolkit")
      .map((tool) => String(tool.config.toolkit ?? "")),
  );

  function matchingCredentials(toolkit: ToolkitCatalogEntry) {
    const provider = toolkit.credentials[0]?.provider;
    return provider
      ? credentials.filter((credential) => credential.provider === provider)
      : [];
  }

  const filtered = visible.filter((toolkit) => {
    const text = `${toolkit.label} ${toolkit.key} ${toolkit.category}`.toLowerCase();
    if (query && !text.includes(query.toLowerCase())) return false;
    const configured = configuredKeys.has(toolkit.key);
    const hasCredential =
      !toolkit.credentials.length || matchingCredentials(toolkit).length > 0;
    if (filter === "configured") return configured;
    if (filter === "needs_setup")
      return toolkit.available && (!configured || !hasCredential);
    if (filter === "unavailable") return !toolkit.available;
    return true;
  });

  function selectToolkit(toolkit: ToolkitCatalogEntry) {
    setSelectedKey(toolkit.key);
    setOptions(optionDefaults(toolkit));
    setCredentialId(matchingCredentials(toolkit)[0]?.id ?? "");
    setSecretLabel(`${toolkit.label} credential`);
    setSecretValue("");
    setMessage(null);
  }

  async function saveCredential() {
    if (!selected?.credentials[0] || !secretLabel.trim() || !secretValue) return;
    setBusy("credential");
    setMessage(null);
    try {
      const created = await createCredential(await getAccessToken(), {
        name: secretLabel.trim(),
        provider: selected.credentials[0].provider,
        value: secretValue,
      });
      setCredentials((current) => [created, ...current]);
      setCredentialId(created.id);
      setSecretValue("");
      setMessage("Credential encrypted and selected.");
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : "Credential save failed");
    } finally {
      setBusy(null);
    }
  }

  async function createToolkitTool() {
    if (!selected) return;
    const credentialRequired = selected.credentials.length > 0;
    if (credentialRequired && !credentialId) {
      setMessage("Select or add the required credential first.");
      return;
    }
    setBusy("tool");
    setMessage(null);
    try {
      const baseSlug = slugifyName(selected.label);
      const duplicateCount = tools.filter((tool) => tool.slug.startsWith(baseSlug)).length;
      const created = await createToolDefinition(await getAccessToken(), {
        name: selected.label,
        slug: duplicateCount ? `${baseSlug}-${duplicateCount + 1}` : baseSlug,
        description: selected.description,
        kind: "python_toolkit",
        httpMethod: null,
        baseUrl: null,
        path: null,
        requestSchema: { type: "object", properties: {} },
        responseDescription: "",
        responseSchema: null,
        headers: {},
        config: {
          toolkit: selected.key,
          options,
          include_tools: [],
          destructive_tools: [],
        },
        credentialId: credentialId || null,
        approvalRequired: selected.side_effects,
        active: true,
        connectionStatus: "unvalidated",
        lastValidatedAt: null,
        lastValidationError: null,
      });
      setTools((current) => [created, ...current]);
      setMessage("Toolkit tool created. Open it to review individual capabilities.");
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : "Tool creation failed");
    } finally {
      setBusy(null);
    }
  }

  const selectedCredentials = selected ? matchingCredentials(selected) : [];
  const selectedTools = selected
    ? tools.filter(
        (tool) =>
          tool.kind === "python_toolkit" &&
          String(tool.config.toolkit ?? "") === selected.key,
      )
    : [];

  return (
    <div className="space-y-6">
      <header>
        <p className="text-xs font-semibold uppercase tracking-[0.14em] text-teal">
          Tools
        </p>
        <h1 className="font-display text-4xl font-semibold tracking-tight">
          Toolkit catalog
        </h1>
        <p className="mt-2 max-w-3xl text-sm text-slate-muted">
          Built-in toolkits (Gmail, Slack, and more). Enabling one creates a
          normal Tool you can attach to agents and teams — same as API or Python
          tools. Manage enabled ones under{" "}
          <Link href="/admin/tools" className="font-medium text-teal underline">
            Tools → Toolkits
          </Link>
          .
        </p>
      </header>

      <section className="grid min-h-[620px] gap-4 lg:grid-cols-[minmax(300px,0.8fr)_minmax(0,1.2fr)]">
        <div className="table-shell flex min-h-0 flex-col rounded-xl">
          <div className="space-y-3 border-b border-line p-3">
            <Input
              aria-label="Search toolkits"
              value={query}
              placeholder="Search Shopify, Slack, SQL…"
              onChange={(event) => setQuery(event.target.value)}
            />
            <div className="flex flex-wrap gap-1.5">
              {(
                [
                  ["all", "All"],
                  ["configured", "Configured"],
                  ["needs_setup", "Needs setup"],
                  ["unavailable", "Unavailable"],
                ] as Array<[Filter, string]>
              ).map(([value, label]) => (
                <button
                  key={value}
                  type="button"
                  onClick={() => setFilter(value)}
                  className={cn(
                    "rounded-md px-2.5 py-1 text-xs font-medium transition",
                    filter === value
                      ? "bg-ink text-canvas"
                      : "bg-fog/70 text-slate-muted hover:text-ink",
                  )}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>
          <div className="max-h-[700px] flex-1 overflow-y-auto">
            {filtered.map((toolkit) => {
              const configured = configuredKeys.has(toolkit.key);
              const credentialReady =
                !toolkit.credentials.length || matchingCredentials(toolkit).length > 0;
              return (
                <button
                  key={toolkit.key}
                  type="button"
                  onClick={() => selectToolkit(toolkit)}
                  className={cn(
                    "flex w-full items-center justify-between gap-3 border-b border-line/60 px-4 py-3 text-left transition last:border-0",
                    selectedKey === toolkit.key
                      ? "bg-ink text-canvas"
                      : "hover:bg-mist/70",
                  )}
                >
                  <div className="flex min-w-0 items-center gap-3">
                    <ToolkitLogo
                      toolkitKey={toolkit.key}
                      label={toolkit.label}
                    />
                    <div className="min-w-0">
                      <p className="truncate text-sm font-medium">
                        {toolkit.label}
                      </p>
                      <p
                        className={cn(
                          "mono-cell truncate",
                          selectedKey === toolkit.key
                            ? "text-canvas/65"
                            : "text-slate-muted",
                        )}
                      >
                        {toolkit.category} · {toolkit.key}
                      </p>
                    </div>
                  </div>
                  <span
                    className={cn(
                      "size-2 shrink-0 rounded-full",
                      configured
                        ? "bg-teal"
                        : !toolkit.available
                          ? "bg-rose"
                          : credentialReady
                            ? "bg-info"
                            : "bg-amber",
                    )}
                    title={
                      configured
                        ? "Configured"
                        : !toolkit.available
                          ? "Unavailable"
                          : credentialReady
                            ? "Ready to create"
                            : "Credential required"
                    }
                  />
                </button>
              );
            })}
            {!filtered.length ? (
              <p className="px-4 py-10 text-center text-sm text-slate-muted">
                No toolkits match this filter.
              </p>
            ) : null}
          </div>
        </div>

        <div className="table-shell rounded-xl">
          {selected ? (
            <>
              <div className="border-b border-line p-5">
                <div className="flex flex-wrap items-start justify-between gap-4">
                  <div className="flex items-start gap-4">
                    <ToolkitLogo
                      toolkitKey={selected.key}
                      label={selected.label}
                      size={44}
                      className="mt-1"
                    />
                    <div>
                      <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-slate-muted">
                        {selected.category}
                      </p>
                      <h2 className="mt-1 font-display text-2xl font-semibold">
                        {selected.label}
                      </h2>
                      <p className="mono-cell mt-1 text-slate-muted">
                        tools.{selected.module}.{selected.class_name}
                      </p>
                    </div>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <Badge
                      dot
                      tone={
                        selected.available
                          ? "success"
                          : selected.disabled_reason || selected.status === "blocked"
                            ? "warning"
                            : "danger"
                      }
                    >
                      {selected.available
                        ? selected.credentials.length
                          ? "Needs credential"
                          : "Ready"
                        : selected.disabled_reason || selected.status === "blocked"
                          ? "Policy"
                          : isMissingPackage(selected)
                            ? "Package missing"
                            : "Unavailable"}
                    </Badge>
                    {selected.side_effects ? (
                      <Badge tone="warning">Writes / approval</Badge>
                    ) : (
                      <Badge tone="info">Read-only default</Badge>
                    )}
                  </div>
                </div>
                <p className="mt-3 text-sm leading-relaxed text-slate-muted">
                  {selected.description}
                </p>
              </div>

              <div className="space-y-6 p-5">
                {!selected.available ? (
                  <section className="rounded-xl border border-rose/25 bg-rose/8 p-4">
                    <h3 className="text-sm font-semibold text-rose">
                      {unavailableTitle(selected)}
                    </h3>
                    <p className="mt-1 text-sm text-slate-muted">
                      {unavailableDetail(selected)}
                    </p>
                    {isMissingPackage(selected) ? (
                      <p className="mt-2 text-xs text-slate-muted">
                        Install the dependency in{" "}
                        <code>apps/backend/pyproject.toml</code> and rebuild the
                        backend image. Packages cannot be installed with tenant
                        credentials.
                      </p>
                    ) : selected.disabled_reason ||
                      selected.status === "blocked" ? (
                      <p className="mt-2 text-xs text-slate-muted">
                        This integration stays hidden from agent attachment until
                        Atlas supports the required credential or runtime model.
                      </p>
                    ) : (
                      <p className="mt-2 text-xs text-slate-muted">
                        Availability is determined by the backend toolkit catalog.
                        Non-secret settings belong in toolkit options; secrets stay
                        in Credentials.
                      </p>
                    )}
                  </section>
                ) : null}

                {selected.credentials[0] ? (
                  <section>
                    <div className="mb-3 flex items-center justify-between gap-3">
                      <div>
                        <h3 className="text-sm font-semibold">Required credential</h3>
                        <p className="text-xs text-slate-muted">
                          Provider: {selected.credentials[0].provider} · runtime variable:{" "}
                          {selected.credentials[0].env_var}
                        </p>
                      </div>
                      <Badge
                        tone={selectedCredentials.length ? "success" : "warning"}
                      >
                        {selectedCredentials.length ? "Ready" : "Missing"}
                      </Badge>
                    </div>
                    {selectedCredentials.length ? (
                      <div>
                        <Label htmlFor="integration-credential">
                          Tenant credential
                        </Label>
                        <SearchableSelect
                          id="integration-credential"
                          value={credentialId}
                          onChange={setCredentialId}
                          placeholder="Select credential"
                          emptyMessage="No matching credentials"
                          options={selectedCredentials.map((credential) => ({
                            value: credential.id,
                            label: credential.name,
                          }))}
                        />
                      </div>
                    ) : (
                      <div className="grid gap-3 rounded-xl border border-line bg-raised/60 p-4 md:grid-cols-[1fr_1.4fr_auto] md:items-end">
                        <div>
                          <Label htmlFor="integration-secret-label">Label</Label>
                          <Input
                            id="integration-secret-label"
                            value={secretLabel}
                            onChange={(event) => setSecretLabel(event.target.value)}
                          />
                        </div>
                        <div>
                          <Label htmlFor="integration-secret">Secret value</Label>
                          <Input
                            id="integration-secret"
                            type="password"
                            autoComplete="off"
                            value={secretValue}
                            onChange={(event) => setSecretValue(event.target.value)}
                          />
                        </div>
                        <Button
                          variant="secondary"
                          disabled={
                            busy !== null || !secretLabel.trim() || !secretValue
                          }
                          onClick={saveCredential}
                        >
                          {busy === "credential" ? "Encrypting…" : "Save credential"}
                        </Button>
                      </div>
                    )}
                  </section>
                ) : (
                  <section className="flex items-center justify-between rounded-xl border border-line bg-raised/60 p-4">
                    <div>
                      <h3 className="text-sm font-semibold">Credential</h3>
                      <p className="text-xs text-slate-muted">
                        No API secret is required for this toolkit.
                      </p>
                    </div>
                    <Badge tone="success">Not required</Badge>
                  </section>
                )}

                <section>
                  <h3 className="text-sm font-semibold">Toolkit options</h3>
                  {Object.keys(selected.options).length ? (
                    <div className="mt-3 grid gap-3 md:grid-cols-2">
                      {Object.entries(selected.options).map(([name, option]) => (
                        <div key={name}>
                          <Label htmlFor={`integration-option-${name}`}>
                            {name.replaceAll("_", " ")}
                          </Label>
                          {option.type === "boolean" ? (
                            <Select
                              id={`integration-option-${name}`}
                              value={String(options[name] ?? option.default ?? false)}
                              onChange={(event) =>
                                setOptions((current) => ({
                                  ...current,
                                  [name]: event.target.value === "true",
                                }))
                              }
                            >
                              <option value="true">Enabled</option>
                              <option value="false">Disabled</option>
                            </Select>
                          ) : (
                            <Input
                              id={`integration-option-${name}`}
                              type={option.type === "integer" ? "number" : "text"}
                              min={option.minimum}
                              max={option.maximum}
                              value={String(options[name] ?? option.default ?? "")}
                              onChange={(event) =>
                                setOptions((current) => ({
                                  ...current,
                                  [name]:
                                    option.type === "integer"
                                      ? Number(event.target.value)
                                      : event.target.value,
                                }))
                              }
                            />
                          )}
                        </div>
                      ))}
                    </div>
                  ) : (
                    <p className="mt-1 text-sm text-slate-muted">
                      This toolkit has no tenant-configurable constructor options.
                    </p>
                  )}
                </section>

                {selectedTools.length ? (
                  <section>
                    <h3 className="mb-2 text-sm font-semibold">
                      Configured definitions
                    </h3>
                    <div className="space-y-2">
                      {selectedTools.map((tool) => (
                        <Link
                          key={tool.id}
                          href={`/admin/tools/${tool.id}`}
                          className="flex items-center justify-between rounded-lg border border-line bg-raised px-3 py-2 text-sm hover:bg-mist"
                        >
                          <span className="font-medium">{tool.name}</span>
                          <span className="text-xs text-teal">Open →</span>
                        </Link>
                      ))}
                    </div>
                  </section>
                ) : null}

                {message ? (
                  <p className="rounded-lg border border-teal/25 bg-teal/8 px-3 py-2 text-sm">
                    {message}
                  </p>
                ) : null}

                <div className="flex flex-wrap items-center justify-between gap-3 border-t border-line pt-4">
                  <p className="max-w-lg text-xs text-slate-muted">
                    Creating a definition makes this toolkit available for attachment
                    to agents. Review included functions and destructive operations in
                    the tool editor afterward.
                  </p>
                  <Button
                    variant="accent"
                    disabled={
                      busy !== null ||
                      !selected.available ||
                      (selected.credentials.length > 0 && !credentialId)
                    }
                    onClick={createToolkitTool}
                  >
                    {busy === "tool" ? "Creating…" : "Create toolkit tool"}
                  </Button>
                </div>
              </div>
            </>
          ) : (
            <div className="flex min-h-[500px] items-center justify-center text-sm text-slate-muted">
              Select a toolkit to review its setup.
            </div>
          )}
        </div>
      </section>
    </div>
  );
}
