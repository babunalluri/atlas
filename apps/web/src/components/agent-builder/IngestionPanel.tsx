"use client";

import { useState } from "react";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Input, Label } from "@/components/ui/Field";
import {
  createKnowledgeBase,
  deleteKnowledgeSource,
  reindexKnowledgeSource,
  testKnowledgeSearch,
  uploadKnowledgeSource,
} from "@/lib/api/admin";
import type {
  KnowledgeBaseSummary,
  KnowledgeSearchResult,
  KnowledgeSource,
} from "@/lib/api/types";
import { useAgentOsToken } from "@/lib/auth/token";
import { formatBytes, formatRelative } from "@/lib/utils";

function tone(status: KnowledgeSource["status"]) {
  if (status === "ready") return "success" as const;
  if (status === "failed") return "danger" as const;
  if (status === "processing" || status === "uploading") return "warning" as const;
  return "neutral" as const;
}

export function IngestionPanel({
  sources,
  initialBases,
}: {
  sources: KnowledgeSource[];
  initialBases: Array<Pick<KnowledgeBaseSummary, "id" | "name">>;
}) {
  const [items, setItems] = useState(sources);
  const [bases, setBases] = useState(initialBases);
  const [knowledgeBaseId, setKnowledgeBaseId] = useState("");
  const [knowledgeBaseName, setKnowledgeBaseName] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<KnowledgeSearchResult[]>([]);
  const { getAccessToken } = useAgentOsToken();

  async function upload() {
    if (!file || !knowledgeBaseId) return;
    setUploading(true);
    setError(null);
    try {
      const source = await uploadKnowledgeSource(
        await getAccessToken(),
        knowledgeBaseId,
        file,
      );
      setItems((current) => [source, ...current]);
      setFile(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Upload failed");
    } finally {
      setUploading(false);
    }
  }

  async function createBase() {
    if (!knowledgeBaseName.trim()) return;
    setError(null);
    try {
      const base = await createKnowledgeBase(
        await getAccessToken(),
        knowledgeBaseName.trim(),
      );
      setKnowledgeBaseId(base.id);
      setBases((current) => [...current, base]);
      setKnowledgeBaseName("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Creation failed");
    }
  }

  async function reindex(sourceId: string) {
    setError(null);
    try {
      const source = await reindexKnowledgeSource(
        await getAccessToken(),
        sourceId,
      );
      setItems((current) =>
        current.map((item) => (item.id === sourceId ? source : item)),
      );
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Reindex failed");
    }
  }

  async function remove(sourceId: string) {
    setError(null);
    try {
      await deleteKnowledgeSource(await getAccessToken(), sourceId);
      setItems((current) => current.filter((item) => item.id !== sourceId));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Delete failed");
    }
  }

  async function search() {
    if (!knowledgeBaseId || !searchQuery.trim()) return;
    setError(null);
    try {
      setSearchResults(
        await testKnowledgeSearch(
          await getAccessToken(),
          knowledgeBaseId,
          searchQuery.trim(),
        ),
      );
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Search failed");
    }
  }

  return (
    <div className="space-y-6">
      <header>
        <p className="text-xs font-semibold uppercase tracking-[0.14em] text-teal">
          Knowledge
        </p>
        <h1 className="font-display text-4xl font-semibold tracking-tight">
          Ingestion status
        </h1>
        <p className="mt-2 max-w-2xl text-sm text-slate-muted">
          Document processing is asynchronous. Agents only retrieve chunks from
          sources that reach a ready state.
        </p>
      </header>

      <section className="surface-panel rounded-2xl p-5">
        <h2 className="font-display text-lg font-semibold">Upload a document</h2>
        <p className="mt-1 text-sm text-slate-muted">
          Text, Markdown, JSON, and PDF files up to the configured tenant limit.
        </p>
        <div className="mt-4 flex max-w-xl items-end gap-2">
          <div className="flex-1">
            <Label htmlFor="knowledge-base-name">New knowledge base</Label>
            <Input
              id="knowledge-base-name"
              value={knowledgeBaseName}
              onChange={(event) => setKnowledgeBaseName(event.target.value)}
              placeholder="Customer support docs"
            />
          </div>
          <Button
            variant="secondary"
            disabled={!knowledgeBaseName.trim()}
            onClick={createBase}
          >
            Create
          </Button>
        </div>
        <div className="mt-4 grid gap-3 md:grid-cols-[1fr_1fr_auto] md:items-end">
          <div>
            <Label htmlFor="knowledge-base-id">Knowledge base ID</Label>
            <select
              id="knowledge-base-id"
              value={knowledgeBaseId}
              onChange={(event) => setKnowledgeBaseId(event.target.value)}
              className="h-10 w-full rounded-md border border-line bg-raised px-3 text-sm"
            >
              <option value="">Select a knowledge base</option>
              {bases.map((base) => (
                <option key={base.id} value={base.id}>
                  {base.name}
                </option>
              ))}
            </select>
          </div>
          <div>
            <Label htmlFor="knowledge-file">Document</Label>
            <Input
              id="knowledge-file"
              type="file"
              accept=".txt,.md,.json,.pdf"
              onChange={(event) => setFile(event.target.files?.[0] ?? null)}
            />
          </div>
          <Button
            variant="accent"
            disabled={!file || !knowledgeBaseId || uploading}
            onClick={upload}
          >
            {uploading ? "Uploading…" : "Upload"}
          </Button>
        </div>
        {error ? <p className="mt-2 text-sm text-rose">{error}</p> : null}
      </section>

      <section className="surface-panel rounded-2xl p-5">
        <h2 className="font-display text-lg font-semibold">
          Test semantic retrieval
        </h2>
        <div className="mt-3 flex gap-2">
          <Input
            value={searchQuery}
            onChange={(event) => setSearchQuery(event.target.value)}
            placeholder="Ask a question grounded in this knowledge base"
          />
          <Button
            variant="secondary"
            disabled={!knowledgeBaseId || !searchQuery.trim()}
            onClick={search}
          >
            Search
          </Button>
        </div>
        <ul className="mt-3 space-y-2">
          {searchResults.map((result) => (
            <li key={result.id} className="rounded-xl border border-line p-3">
              <p className="text-xs font-semibold text-teal">
                Score {result.score.toFixed(3)}
              </p>
              <p className="mt-1 text-sm">{result.content}</p>
            </li>
          ))}
        </ul>
      </section>

      <div className="table-shell rounded-xl">
        <div className="grid grid-cols-[1.5fr_0.7fr_0.6fr_0.6fr_0.8fr] gap-3 border-b border-line px-4 py-2.5">
          <span className="th-label">Source</span>
          <span className="th-label text-right">Size</span>
          <span className="th-label">Status</span>
          <span className="th-label text-right">Updated</span>
          <span className="th-label">Actions</span>
        </div>
        <ul>
          {items.map((source) => (
            <li
              key={source.id}
              className="grid grid-cols-[1.5fr_0.7fr_0.6fr_0.6fr_0.8fr] items-center gap-3 border-b border-line/60 px-4 py-2.5 last:border-0"
            >
              <div className="min-w-0">
                <p className="truncate text-sm font-medium">{source.name}</p>
                <p className="mono-cell truncate text-slate-muted">
                  {source.mimeType}
                </p>
                {source.errorMessage ? (
                  <p className="text-xs text-rose">{source.errorMessage}</p>
                ) : null}
              </div>
              <p className="mono-cell text-right">{formatBytes(source.byteSize)}</p>
              <div>
                <Badge
                  dot
                  live={
                    source.status === "processing" ||
                    source.status === "uploading"
                  }
                  tone={tone(source.status)}
                >
                  {source.status}
                </Badge>
              </div>
              <p className="mono-cell text-right text-slate-muted">
                {formatRelative(source.updatedAt)}
              </p>
              <div className="flex gap-1">
                <Button
                  size="sm"
                  variant="secondary"
                  onClick={() => void reindex(source.id)}
                >
                  Reindex
                </Button>
                <Button
                  size="sm"
                  variant="danger"
                  onClick={() => void remove(source.id)}
                >
                  Delete
                </Button>
              </div>
            </li>
          ))}
          {items.length === 0 ? (
            <li className="px-4 py-10 text-center text-sm text-slate-muted">
              No sources yet.
            </li>
          ) : null}
        </ul>
      </div>
    </div>
  );
}
