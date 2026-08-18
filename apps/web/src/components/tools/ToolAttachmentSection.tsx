"use client";

import { useMemo, useState } from "react";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { MinusIcon } from "@/components/ui/icons";
import { Input } from "@/components/ui/Field";
import {
  TOOL_CATALOG,
  type ToolBinding,
  type ToolDefinition,
  type ToolKind,
} from "@/lib/api/types";

function capabilityNames(definition: ToolDefinition): string[] {
  const caps = definition.config.capabilities;
  if (!Array.isArray(caps)) return [];
  return caps
    .map((item) =>
      item && typeof item === "object" && "name" in item
        ? String((item as { name: unknown }).name)
        : "",
    )
    .filter(Boolean);
}

function reusableSummary(definition: ToolDefinition): string {
  if (definition.kind === "http") {
    return `${definition.httpMethod ?? "HTTP"} ${definition.baseUrl ?? ""}${
      definition.path ?? ""
    }`;
  }
  const kindLabel = definition.kind.replaceAll("_", " ");
  // tenant_python: agent attach uses all published methods — no per-binding picker.
  if (definition.kind === "tenant_python") {
    const names = capabilityNames(definition);
    if (names.length > 0) {
      return `${kindLabel} · ${names.length} capabilities (${names.join(", ")})`;
    }
    // Empty list is still valid: runtime AST-discovers async methods from source.
    return `${kindLabel} · all source capabilities`;
  }
  const selected =
    (definition.config.allowed_operations as string[] | undefined) ??
    (definition.config.include_tools as string[] | undefined) ??
    [];
  // Toolkit / custom_python: empty include_tools means all capabilities at runtime.
  if (
    (definition.kind === "python_toolkit" ||
      definition.kind === "custom_python") &&
    selected.length === 0
  ) {
    return `${kindLabel} · all capabilities`;
  }
  return `${kindLabel} · ${selected.length} selected capabilities`;
}

export function ToolAttachmentSection({
  tools,
  onChange,
  toolDefinitions = [],
  credentialNames = {},
  description,
}: {
  tools: ToolBinding[];
  onChange: (tools: ToolBinding[]) => void;
  toolDefinitions?: ToolDefinition[];
  credentialNames?: Record<string, string>;
  description?: string;
}) {
  const [query, setQuery] = useState("");
  const [scope, setScope] = useState<"all" | "builtin" | "tenant">("all");

  const enabled = useMemo(() => tools.filter((tool) => tool.enabled), [tools]);

  const enabledBuiltinKinds = useMemo(
    () =>
      new Set(
        enabled.filter((tool) => !tool.definitionId).map((tool) => tool.kind),
      ),
    [enabled],
  );
  const enabledDefinitionIds = useMemo(
    () =>
      new Set(
        enabled
          .map((tool) => tool.definitionId)
          .filter((id): id is string => Boolean(id)),
      ),
    [enabled],
  );

  const definitionById = useMemo(
    () => new Map(toolDefinitions.map((item) => [item.id, item])),
    [toolDefinitions],
  );

  const availableBuiltin = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (scope === "tenant") return [];
    return TOOL_CATALOG.filter((tool) => {
      if (enabledBuiltinKinds.has(tool.kind)) return false;
      if (!q) return true;
      return (
        tool.label.toLowerCase().includes(q) ||
        tool.description.toLowerCase().includes(q) ||
        tool.kind.toLowerCase().includes(q)
      );
    });
  }, [enabledBuiltinKinds, query, scope]);

  const availableTenant = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (scope === "builtin") return [];
    return toolDefinitions.filter((definition) => {
      if (!definition.active) return false;
      // Editable Python tools are only runnable after Publish pins a version.
      if (
        definition.kind === "tenant_python" &&
        !definition.publishedVersionId
      ) {
        return false;
      }
      if (enabledDefinitionIds.has(definition.id)) return false;
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
  }, [enabledDefinitionIds, query, scope, toolDefinitions]);

  function setTools(next: ToolBinding[]) {
    onChange(next);
  }

  function addBuiltin(kind: ToolKind) {
    const catalog = TOOL_CATALOG.find((tool) => tool.kind === kind);
    if (!catalog) return;
    const existing = tools.find((tool) => tool.kind === kind && !tool.definitionId);
    if (existing) {
      setTools(
        tools.map((tool) =>
          tool.kind === kind && !tool.definitionId
            ? { ...tool, enabled: true }
            : tool,
        ),
      );
      return;
    }
    setTools([
      ...tools,
      {
        id: `tool_${kind}`,
        kind,
        label: catalog.label,
        enabled: true,
        config: {},
        requiresApproval: catalog.requiresApproval,
      },
    ]);
  }

  function addTenant(definition: ToolDefinition) {
    const existing = tools.find((tool) => tool.definitionId === definition.id);
    if (existing) {
      setTools(
        tools.map((tool) =>
          tool.definitionId === definition.id ? { ...tool, enabled: true } : tool,
        ),
      );
      return;
    }
    setTools([
      ...tools,
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
    ]);
  }

  function removeTool(tool: ToolBinding) {
    setTools(
      tools.map((item) => {
        if (tool.definitionId) {
          return item.definitionId === tool.definitionId
            ? { ...item, enabled: false }
            : item;
        }
        return item.kind === tool.kind && !item.definitionId
          ? { ...item, enabled: false }
          : item;
      }),
    );
  }

  function updateConfig(kind: ToolKind, key: string, value: string) {
    setTools(
      tools.map((tool) =>
        tool.kind === kind && !tool.definitionId
          ? { ...tool, config: { ...tool.config, [key]: value } }
          : tool,
      ),
    );
  }

  const availableCount = availableBuiltin.length + availableTenant.length;

  return (
    <section className="rounded-xl border border-line bg-raised/40 p-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-semibold">Tools</h2>
        <Badge tone={enabled.length > 0 ? "success" : "neutral"}>
          {enabled.length} selected
        </Badge>
      </div>
      {description ? (
        <p className="mb-3 text-xs text-slate-muted">{description}</p>
      ) : null}

      {enabled.length === 0 ? (
        <p className="mb-3 rounded-md border border-dashed border-line px-3 py-4 text-center text-sm text-slate-muted">
          No tools yet — add tools below.
        </p>
      ) : (
        <ul className="mb-3 space-y-1.5">
          {enabled.map((tool) => {
            const definition = tool.definitionId
              ? definitionById.get(tool.definitionId)
              : undefined;
            const displayLabel = definition?.name ?? tool.label;
            const summary = definition
              ? reusableSummary(definition)
              : TOOL_CATALOG.find((item) => item.kind === tool.kind)
                  ?.description;
            const needsApproval =
              tool.requiresApproval ||
              Boolean(
                definition &&
                  (definition.approvalRequired ||
                    (definition.kind === "http" &&
                      definition.httpMethod !== "GET")),
              );
            return (
              <li
                key={tool.definitionId ?? `builtin_${tool.kind}`}
                className="rounded-md border border-line bg-canvas/40 px-2.5 py-2"
              >
                <div className="flex items-center gap-2">
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <p className="truncate text-sm font-medium">{displayLabel}</p>
                      <span className="text-[10px] uppercase tracking-wide text-slate-muted">
                        {tool.definitionId ? "tenant" : "built-in"}
                      </span>
                      {needsApproval ? (
                        <Badge tone="warning">approval</Badge>
                      ) : null}
                      {definition && !definition.active ? (
                        <Badge tone="neutral">inactive</Badge>
                      ) : null}
                    </div>
                    {summary ? (
                      <p
                        className="truncate text-xs text-slate-muted"
                        title={summary}
                      >
                        {summary}
                        {definition?.credentialId
                          ? ` · ${credentialNames[definition.credentialId] ?? "credential"}`
                          : ""}
                      </p>
                    ) : null}
                  </div>
                  <Button
                    size="sm"
                    variant="ghost"
                    icon={<MinusIcon />}
                    onClick={() => removeTool(tool)}
                  >
                    Remove
                  </Button>
                </div>
                {!tool.definitionId && tool.kind.startsWith("rest_") ? (
                  <div className="mt-2 grid gap-2 sm:grid-cols-2">
                    <Input
                      aria-label={`${tool.label} base URL`}
                      value={String(tool.config.base_url ?? "")}
                      placeholder="https://api.example.com/v1"
                      onChange={(event) =>
                        updateConfig(tool.kind, "base_url", event.target.value)
                      }
                    />
                    <Input
                      aria-label={`${tool.label} credential ID`}
                      value={String(tool.config.credential_id ?? "")}
                      placeholder="Optional credential UUID"
                      onChange={(event) =>
                        updateConfig(
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
        </ul>
      )}

      <div className="border-t border-line pt-3">
        <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-muted">
          Add tools
        </p>
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <Input
            value={query}
            placeholder="Search tools…"
            className="min-w-[180px] flex-1"
            onChange={(event) => setQuery(event.target.value)}
          />
          {(
            [
              ["all", "All"],
              ["builtin", "Built-in"],
              ["tenant", "Tenant"],
            ] as const
          ).map(([value, label]) => (
            <button
              key={value}
              type="button"
              onClick={() => setScope(value)}
              className={
                scope === value
                  ? "rounded-md bg-ink px-2.5 py-1.5 text-xs font-medium text-canvas"
                  : "rounded-md bg-raised px-2.5 py-1.5 text-xs font-medium text-slate-muted hover:bg-mist"
              }
            >
              {label}
            </button>
          ))}
        </div>

        {availableCount === 0 ? (
          <p className="text-sm text-slate-muted">
            {toolDefinitions.length === 0 && scope !== "builtin"
              ? "No tenant tools yet — create them under Admin → Tools."
              : query.trim()
                ? "No tools match this search."
                : "All matching tools are already attached."}
          </p>
        ) : (
          <div className="flex max-h-56 flex-wrap gap-1.5 overflow-y-auto">
            {availableBuiltin.map((tool) => (
              <button
                key={tool.kind}
                type="button"
                onClick={() => addBuiltin(tool.kind)}
                className="rounded-md border border-line bg-raised px-2.5 py-1.5 text-left text-sm hover:border-teal/50"
                title={tool.description}
              >
                <span className="font-medium">{tool.label}</span>
                <span className="ml-1.5 text-xs text-slate-muted">+ Add</span>
              </button>
            ))}
            {availableTenant.map((definition) => (
              <button
                key={definition.id}
                type="button"
                onClick={() => addTenant(definition)}
                className="rounded-md border border-line bg-raised px-2.5 py-1.5 text-left text-sm hover:border-teal/50"
                title={reusableSummary(definition)}
              >
                <span className="font-medium">{definition.name}</span>
                <span className="ml-1.5 text-xs text-slate-muted">+ Add</span>
              </button>
            ))}
          </div>
        )}
      </div>
    </section>
  );
}
