"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/Button";
import { Badge } from "@/components/ui/Badge";
import {
  CatalogControls,
  DEFAULT_CATALOG_QUERY,
  type CatalogQuery,
} from "@/components/ui/CatalogControls";
import { Input, Label } from "@/components/ui/Field";
import { createAgent, getAgent, listAgentCatalog } from "@/lib/api/admin";
import type {
  AgentConfig,
  AgentSummary,
  CatalogPage,
  ToolBinding,
} from "@/lib/api/types";
import { useAgentOsToken } from "@/lib/auth/token";
import { slugifyName } from "@/lib/validation/agent-form";
import { cn, formatRelative } from "@/lib/utils";

function statusTone(status: AgentSummary["status"]) {
  if (status === "published") return "success" as const;
  if (status === "archived") return "neutral" as const;
  return "warning" as const;
}

function ToolRow({ tool }: { tool: ToolBinding }) {
  return (
    <li className="rounded-lg border border-line/70 bg-raised/80 px-3 py-2.5">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate text-sm font-medium text-ink">{tool.label}</p>
          <p className="mono-cell truncate text-slate-muted">{tool.kind}</p>
        </div>
        <div className="flex shrink-0 flex-col items-end gap-1">
          <Badge tone={tool.enabled ? "success" : "neutral"} dot>
            {tool.enabled ? "On" : "Off"}
          </Badge>
          {tool.requiresApproval ? (
            <Badge tone="warning">Approval</Badge>
          ) : null}
        </div>
      </div>
    </li>
  );
}

export function AgentList({
  initial,
}: {
  initial: CatalogPage<AgentSummary>;
}) {
  const router = useRouter();
  const { getAccessToken } = useAgentOsToken();
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState<CatalogQuery>(DEFAULT_CATALOG_QUERY);
  const [pageData, setPageData] = useState(initial);
  const [loading, setLoading] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(
    initial.items[0]?.id ?? null,
  );
  const [detail, setDetail] = useState<AgentConfig | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);

  useEffect(() => {
    const handle = window.setTimeout(() => {
      let cancelled = false;
      setLoading(true);
      void (async () => {
        try {
          const next = await listAgentCatalog(await getAccessToken(), {
            q: query.q,
            status: query.status,
            page: query.page,
            pageSize: query.pageSize,
          });
          if (cancelled) return;
          setPageData(next);
          if (!next.items.some((item) => item.id === selectedId)) {
            setSelectedId(next.items[0]?.id ?? null);
          }
        } catch (reason) {
          if (!cancelled) {
            setError(
              reason instanceof Error ? reason.message : "Failed to load agents",
            );
          }
        } finally {
          if (!cancelled) setLoading(false);
        }
      })();
      return () => {
        cancelled = true;
      };
    }, 250);
    return () => window.clearTimeout(handle);
  }, [getAccessToken, query, selectedId]);

  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      return;
    }
    let cancelled = false;
    setDetailLoading(true);
    setDetailError(null);
    void (async () => {
      try {
        const next = await getAgent(await getAccessToken(), selectedId);
        if (!cancelled) setDetail(next);
      } catch (reason) {
        if (!cancelled) {
          setDetail(null);
          setDetailError(
            reason instanceof Error ? reason.message : "Failed to load agent",
          );
        }
      } finally {
        if (!cancelled) setDetailLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [getAccessToken, selectedId]);

  async function onCreate() {
    setBusy(true);
    setError(null);
    try {
      const token = await getAccessToken();
      const slug = slugifyName(name || "untitled-agent");
      const created = await createAgent(token, {
        name: name.trim() || "Untitled agent",
        slug,
      });
      router.push(`/admin/agents/${created.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create agent");
    } finally {
      setBusy(false);
    }
  }

  const agents = pageData.items;
  const selectedSummary = agents.find((agent) => agent.id === selectedId);

  return (
    <div className="space-y-8">
      <section className="surface-panel relative overflow-hidden rounded-2xl p-6 md:p-8">
        <div className="pointer-events-none absolute inset-0 grid-noise opacity-60" />
        <div className="relative grid gap-6 md:grid-cols-[1.4fr_1fr] md:items-end">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.16em] text-teal">
              Tenant fleet
            </p>
            <h1 className="mt-2 font-display text-4xl font-semibold tracking-tight text-ink md:text-5xl">
              Configure agents your customers will recognize.
            </h1>
            <p className="mt-3 max-w-xl text-sm leading-relaxed text-slate-muted">
              Search and page through large agent catalogs, then open one to
              inspect tools and publish versions.
            </p>
          </div>
          <div className="rounded-xl border border-line bg-raised/90 p-4">
            <Label htmlFor="new-agent">New agent</Label>
            <div className="flex gap-2">
              <Input
                id="new-agent"
                placeholder="e.g. Claims navigator"
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
              <Button onClick={onCreate} disabled={busy} variant="accent">
                {busy ? "Creating…" : "Create"}
              </Button>
            </div>
            {error ? <p className="mt-2 text-xs text-rose">{error}</p> : null}
          </div>
        </div>
      </section>

      <section className="grid gap-4 lg:grid-cols-[minmax(0,1.35fr)_minmax(280px,0.85fr)]">
        <div className="table-shell rounded-xl">
          <CatalogControls
            query={query}
            total={pageData.total}
            noun="agents"
            loading={loading}
            onChange={setQuery}
          />
          <div className="grid grid-cols-[1.4fr_0.7fr_0.7fr_0.6fr] gap-3 border-b border-line px-4 py-2.5">
            <span className="th-label">Agent</span>
            <span className="th-label">Model</span>
            <span className="th-label">Status</span>
            <span className="th-label text-right">Updated</span>
          </div>
          <ul>
            {agents.map((agent) => {
              const active = agent.id === selectedId;
              return (
                <li key={agent.id} className="border-b border-line/60 last:border-0">
                  <button
                    type="button"
                    onClick={() => setSelectedId(agent.id)}
                    className={cn(
                      "grid w-full grid-cols-[1.4fr_0.7fr_0.7fr_0.6fr] items-center gap-3 px-4 py-2.5 text-left transition",
                      active ? "bg-ink text-canvas" : "hover:bg-mist/70",
                    )}
                  >
                    <div className="min-w-0">
                      <p className="truncate text-sm font-medium">{agent.name}</p>
                      <p
                        className={cn(
                          "mono-cell truncate",
                          active ? "text-canvas/70" : "text-slate-muted",
                        )}
                      >
                        /{agent.slug}
                      </p>
                    </div>
                    <p
                      className={cn(
                        "mono-cell",
                        active ? "text-canvas/80" : "text-ink-soft",
                      )}
                    >
                      {agent.model}
                    </p>
                    <div className="flex items-center gap-2">
                      <Badge
                        dot
                        tone={statusTone(agent.status)}
                        className={active ? "bg-canvas/15 text-canvas" : undefined}
                      >
                        {agent.status}
                      </Badge>
                      {agent.publishedVersion ? (
                        <span
                          className={cn(
                            "mono-cell",
                            active ? "text-canvas/70" : "text-slate-muted",
                          )}
                        >
                          v{agent.publishedVersion}
                        </span>
                      ) : null}
                    </div>
                    <p
                      className={cn(
                        "mono-cell text-right",
                        active ? "text-canvas/70" : "text-slate-muted",
                      )}
                    >
                      {formatRelative(agent.updatedAt)}
                    </p>
                  </button>
                </li>
              );
            })}
            {agents.length === 0 ? (
              <li className="px-4 py-10 text-center text-sm text-slate-muted">
                No agents match this search.
              </li>
            ) : null}
          </ul>
        </div>

        <aside className="table-shell flex min-h-[320px] flex-col rounded-xl">
          {selectedSummary ? (
            <>
              <div className="border-b border-line px-4 py-3">
                <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-slate-muted">
                  Selected agent
                </p>
                <div className="mt-1 flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <h2 className="truncate font-display text-xl font-semibold">
                      {selectedSummary.name}
                    </h2>
                    <p className="mono-cell truncate text-slate-muted">
                      /{selectedSummary.slug}
                    </p>
                  </div>
                  <Link
                    href={`/admin/agents/${selectedSummary.id}`}
                    className="shrink-0 rounded-md border border-line bg-raised px-2.5 py-1 text-xs font-medium text-ink hover:bg-mist"
                  >
                    Open editor
                  </Link>
                </div>
              </div>
              <div className="flex-1 space-y-3 px-4 py-4">
                <div className="flex items-center justify-between gap-2">
                  <h3 className="text-sm font-semibold text-ink">Tools</h3>
                  {detail && !detailLoading ? (
                    <span className="mono-cell text-slate-muted">
                      {detail.tools.length} attached
                    </span>
                  ) : null}
                </div>
                {detailLoading ? (
                  <p className="text-sm text-slate-muted">Loading tools…</p>
                ) : detailError ? (
                  <p className="text-sm text-rose">{detailError}</p>
                ) : detail && detail.tools.length > 0 ? (
                  <ul className="space-y-2">
                    {detail.tools.map((tool) => (
                      <ToolRow key={tool.id} tool={tool} />
                    ))}
                  </ul>
                ) : (
                  <p className="rounded-lg border border-dashed border-line px-3 py-6 text-center text-sm text-slate-muted">
                    No tools attached yet. Open the editor to bind tools.
                  </p>
                )}
                {detail?.knowledgeBase ? (
                  <div className="rounded-lg border border-line/70 bg-raised/80 px-3 py-2.5">
                    <p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-muted">
                      Knowledge
                    </p>
                    <p className="mt-1 text-sm font-medium text-ink">
                      {detail.knowledgeBase.name}
                    </p>
                  </div>
                ) : null}
              </div>
            </>
          ) : (
            <div className="flex flex-1 items-center justify-center px-4 py-10 text-center text-sm text-slate-muted">
              Select an agent to inspect its tools.
            </div>
          )}
        </aside>
      </section>
    </div>
  );
}
