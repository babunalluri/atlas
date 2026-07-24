"use client";

import { useEffect, useMemo, useState } from "react";

import { Button } from "@/components/ui/Button";
import { Label, Select } from "@/components/ui/Field";
import {
  getWorkspaceInfo,
  listAgentCatalog,
  listTeamCatalog,
  listWorkflowCatalog,
} from "@/lib/api/admin";
import {
  appOrigin,
  buildEmbedSnippets,
  type EmbedKind,
} from "@/lib/embed/snippets";
import { useAgentOsToken } from "@/lib/auth/token";

type CatalogItem = { id: string; name: string; slug: string };

const KIND_OPTIONS: { value: EmbedKind; label: string }[] = [
  { value: "agent", label: "Agent" },
  { value: "team", label: "Team" },
  { value: "workflow", label: "Workflow" },
];

export function PublicApiShareEmbed() {
  const { getAccessToken } = useAgentOsToken();
  const [kind, setKind] = useState<EmbedKind>("agent");
  const [items, setItems] = useState<CatalogItem[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [tenantSlug, setTenantSlug] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState<"link" | "iframe" | "script" | null>(
    null,
  );

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      setLoading(true);
      setError(null);
      try {
        const token = await getAccessToken();
        const [workspace, catalog] = await Promise.all([
          getWorkspaceInfo(token),
          kind === "team"
            ? listTeamCatalog(token, { status: "published", pageSize: 100 })
            : kind === "workflow"
              ? listWorkflowCatalog(token, {
                  status: "published",
                  pageSize: 100,
                })
              : listAgentCatalog(token, { status: "published", pageSize: 100 }),
        ]);
        if (cancelled) return;
        setTenantSlug(workspace.slug);
        const nextItems = catalog.items.map((item) => ({
          id: item.id,
          name: item.name,
          slug: item.slug,
        }));
        setItems(nextItems);
        setSelectedId(nextItems[0]?.id ?? "");
      } catch (reason) {
        if (!cancelled) {
          setItems([]);
          setSelectedId("");
          setError(
            reason instanceof Error
              ? reason.message
              : "Could not load published items",
          );
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [getAccessToken, kind]);

  const selected = items.find((item) => item.id === selectedId) ?? null;

  const snippets = useMemo(() => {
    if (!tenantSlug || !selected) return null;
    return buildEmbedSnippets(tenantSlug, kind, selected.slug, appOrigin());
  }, [kind, selected, tenantSlug]);

  async function copy(label: "link" | "iframe" | "script", value: string) {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(label);
      window.setTimeout(() => setCopied(null), 1600);
    } catch {
      setError("Could not copy to clipboard");
    }
  }

  return (
    <section className="rounded-xl border border-line bg-raised/40 p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold tracking-tight">Share / Embed</h2>
          <p className="mt-1 max-w-2xl text-sm text-slate-muted">
            Customers can chat without joining your Clerk org. Pick a published
            agent, team, or workflow, then drop the hosted link, iframe, or
            script on your site. The widget only talks to public chat
            endpoints — no admin credentials are embedded.
          </p>
        </div>
        {snippets ? (
          <a
            href={snippets.chatUrl}
            target="_blank"
            rel="noreferrer"
            className="text-xs font-medium text-teal hover:underline"
          >
            Open hosted chat →
          </a>
        ) : null}
      </div>

      <div className="mt-4 grid gap-3 sm:grid-cols-2">
        <div>
          <Label htmlFor="share-kind">Target type</Label>
          <Select
            id="share-kind"
            value={kind}
            onChange={(event) => setKind(event.target.value as EmbedKind)}
          >
            {KIND_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </Select>
        </div>
        <div>
          <Label htmlFor="share-resource">
            Published {kind}
          </Label>
          <Select
            id="share-resource"
            value={selectedId}
            disabled={loading || items.length === 0}
            onChange={(event) => setSelectedId(event.target.value)}
          >
            {loading ? (
              <option value="">Loading…</option>
            ) : items.length === 0 ? (
              <option value="">No published {kind}s</option>
            ) : (
              items.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name} ({item.slug})
                </option>
              ))
            )}
          </Select>
        </div>
      </div>

      {!loading && items.length === 0 ? (
        <p className="mt-3 rounded-md border border-amber/30 bg-amber/10 px-3 py-2 text-sm text-amber">
          Publish a {kind} before sharing. Drafts are never available on the
          public chat or embed URLs.
        </p>
      ) : null}

      {error ? <p className="mt-3 text-sm text-rose">{error}</p> : null}

      {snippets ? (
        <div className="mt-4 space-y-3">
          <div>
            <div className="mb-1 flex items-center justify-between gap-2">
              <p className="text-xs font-semibold uppercase tracking-[0.12em] text-slate-muted">
                Hosted link
              </p>
              <Button
                variant="secondary"
                size="sm"
                onClick={() => void copy("link", snippets.chatUrl)}
              >
                {copied === "link" ? "Copied" : "Copy"}
              </Button>
            </div>
            <pre className="overflow-x-auto rounded-lg border border-line bg-canvas/60 p-3 text-xs text-ink">
              {snippets.chatUrl}
            </pre>
          </div>
          <div>
            <div className="mb-1 flex items-center justify-between gap-2">
              <p className="text-xs font-semibold uppercase tracking-[0.12em] text-slate-muted">
                Iframe
              </p>
              <Button
                variant="secondary"
                size="sm"
                onClick={() => void copy("iframe", snippets.iframe)}
              >
                {copied === "iframe" ? "Copied" : "Copy"}
              </Button>
            </div>
            <pre className="overflow-x-auto rounded-lg border border-line bg-canvas/60 p-3 text-xs text-ink">
              {snippets.iframe}
            </pre>
          </div>
          <div>
            <div className="mb-1 flex items-center justify-between gap-2">
              <p className="text-xs font-semibold uppercase tracking-[0.12em] text-slate-muted">
                Script snippet
              </p>
              <Button
                variant="secondary"
                size="sm"
                onClick={() => void copy("script", snippets.script)}
              >
                {copied === "script" ? "Copied" : "Copy"}
              </Button>
            </div>
            <pre className="overflow-x-auto rounded-lg border border-line bg-canvas/60 p-3 text-xs text-ink">
              {snippets.script}
            </pre>
          </div>
        </div>
      ) : null}
    </section>
  );
}
