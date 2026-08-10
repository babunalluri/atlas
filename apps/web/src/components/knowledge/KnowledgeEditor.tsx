"use client";

import Link from "next/link";
import { useState } from "react";

import { BackLink } from "@/components/ui/BackLink";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { EditorActions } from "@/components/ui/EditorActions";
import { Input, Label, Select } from "@/components/ui/Field";
import { SaveIcon } from "@/components/ui/icons";
import {
  deleteKnowledgeSource,
  ingestKnowledgeGithub,
  ingestKnowledgeS3,
  ingestKnowledgeUrl,
  reindexKnowledgeSource,
  testKnowledgeSearch,
  updateKnowledgeBase,
  uploadKnowledgeSource,
} from "@/lib/api/admin";
import type {
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

export function KnowledgeEditor({
  knowledgeBase,
  initialSources,
}: {
  knowledgeBase: { id: string; name: string };
  initialSources: KnowledgeSource[];
}) {
  const { getAccessToken } = useAgentOsToken();
  const [savedName, setSavedName] = useState(knowledgeBase.name);
  const [name, setName] = useState(knowledgeBase.name);
  const [items, setItems] = useState(initialSources);
  const [file, setFile] = useState<File | null>(null);
  const [connector, setConnector] = useState<"upload" | "url" | "s3" | "github">(
    "upload",
  );
  const [urlValue, setUrlValue] = useState("");
  const [s3Uri, setS3Uri] = useState("");
  const [githubRepo, setGithubRepo] = useState("");
  const [githubPath, setGithubPath] = useState("");
  const [githubRef, setGithubRef] = useState("main");
  const [uploading, setUploading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [banner, setBanner] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<KnowledgeSearchResult[]>(
    [],
  );
  const [searching, setSearching] = useState(false);

  const readyCount = items.filter((item) => item.status === "ready").length;
  const dirty = name.trim() !== savedName;

  async function saveName() {
    const next = name.trim();
    if (!next) {
      setError("Name is required");
      return;
    }
    setSaving(true);
    setError(null);
    setBanner(null);
    try {
      const updated = await updateKnowledgeBase(
        await getAccessToken(),
        knowledgeBase.id,
        { name: next },
      );
      setName(updated.name);
      setSavedName(updated.name);
      setBanner("Saved");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Save failed");
    } finally {
      setSaving(false);
    }
  }

  async function upload() {
    if (!file) return;
    setUploading(true);
    setError(null);
    try {
      const source = await uploadKnowledgeSource(
        await getAccessToken(),
        knowledgeBase.id,
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

  async function ingest() {
    setUploading(true);
    setError(null);
    try {
      const token = await getAccessToken();
      let source;
      if (connector === "url") {
        source = await ingestKnowledgeUrl(token, knowledgeBase.id, urlValue.trim());
        setUrlValue("");
      } else if (connector === "s3") {
        source = await ingestKnowledgeS3(token, knowledgeBase.id, s3Uri.trim());
        setS3Uri("");
      } else if (connector === "github") {
        source = await ingestKnowledgeGithub(token, knowledgeBase.id, {
          repo: githubRepo.trim(),
          path: githubPath.trim(),
          ref: githubRef.trim() || "main",
        });
        setGithubPath("");
      } else {
        return;
      }
      setItems((current) => [source, ...current]);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Ingest failed");
    } finally {
      setUploading(false);
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
    if (!searchQuery.trim()) return;
    setSearching(true);
    setError(null);
    try {
      setSearchResults(
        await testKnowledgeSearch(
          await getAccessToken(),
          knowledgeBase.id,
          searchQuery.trim(),
        ),
      );
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Search failed");
    } finally {
      setSearching(false);
    }
  }

  return (
    <div className="space-y-3">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <p className="text-xs text-slate-muted">
            <Link href="/admin/knowledge" className="hover:text-ink">
              Knowledge
            </Link>
            <span className="mx-1.5">/</span>
            {name || "Untitled knowledge"}
          </p>
          <div className="flex min-w-0 items-center gap-1.5">
            <BackLink href="/admin/knowledge" label="Back to knowledge" />
            <h1 className="min-w-0 truncate py-0.5 font-display text-2xl font-semibold leading-snug tracking-tight">
              {name || "Untitled knowledge"}
            </h1>
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-slate-muted">
            <Badge tone={readyCount > 0 ? "success" : "neutral"}>
              {readyCount}/{items.length} ready
            </Badge>
            <span className="mono-cell">{knowledgeBase.id}</span>
          </div>
        </div>
        <EditorActions>
          <Button
            variant="accent"
            size="sm"
            disabled={saving || !dirty}
            onClick={() => void saveName()}
          >
            <SaveIcon />
            {saving ? "Saving…" : "Save"}
          </Button>
        </EditorActions>
      </header>

      {banner ? (
        <p className="rounded-md border border-teal/30 bg-teal/10 px-3 py-1.5 text-sm">
          {banner}
        </p>
      ) : null}
      {error ? (
        <p className="rounded-md border border-rose/30 bg-rose/10 px-3 py-1.5 text-sm text-rose">
          {error}
        </p>
      ) : null}

      <section className="rounded-xl border border-line bg-raised/40 p-3">
        <Label htmlFor="knowledge-name">Name</Label>
        <Input
          id="knowledge-name"
          value={name}
          onChange={(event) => setName(event.target.value)}
          placeholder="Customer support docs"
          onKeyDown={(event) => {
            if (event.key === "Enter") void saveName();
          }}
        />
      </section>

      <section className="rounded-xl border border-line bg-raised/40 p-3">
        <div className="mb-2 flex items-center justify-between gap-2">
          <h2 className="text-sm font-semibold">Add source</h2>
          <p className="text-xs text-slate-muted">
            Upload or connect URL / S3 / GitHub
          </p>
        </div>
        <div className="mb-3 max-w-xs">
          <Label htmlFor="knowledge-connector">Connector</Label>
          <Select
            id="knowledge-connector"
            value={connector}
            onChange={(event) =>
              setConnector(
                event.target.value as "upload" | "url" | "s3" | "github",
              )
            }
          >
            <option value="upload">File upload</option>
            <option value="url">URL</option>
            <option value="s3">Document store URI</option>
            <option value="github">GitHub file</option>
          </Select>
        </div>
        {connector === "upload" ? (
          <div className="flex flex-wrap items-end gap-2">
            <div className="min-w-[220px] flex-1">
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
              disabled={!file || uploading}
              onClick={() => void upload()}
            >
              {uploading ? "Uploading…" : "Upload"}
            </Button>
          </div>
        ) : null}
        {connector === "url" ? (
          <div className="flex flex-wrap items-end gap-2">
            <div className="min-w-[220px] flex-1">
              <Label htmlFor="knowledge-url">Page URL</Label>
              <Input
                id="knowledge-url"
                value={urlValue}
                onChange={(event) => setUrlValue(event.target.value)}
                placeholder="https://example.com/docs"
              />
            </div>
            <Button
              variant="accent"
              disabled={!urlValue.trim() || uploading}
              onClick={() => void ingest()}
            >
              {uploading ? "Ingesting…" : "Ingest URL"}
            </Button>
          </div>
        ) : null}
        {connector === "s3" ? (
          <div className="flex flex-wrap items-end gap-2">
            <div className="min-w-[220px] flex-1">
              <Label htmlFor="knowledge-s3">Existing store URI</Label>
              <Input
                id="knowledge-s3"
                value={s3Uri}
                onChange={(event) => setS3Uri(event.target.value)}
                placeholder="s3://bucket/tenant/file.md or file:///…"
              />
            </div>
            <Button
              variant="accent"
              disabled={!s3Uri.trim() || uploading}
              onClick={() => void ingest()}
            >
              {uploading ? "Ingesting…" : "Ingest URI"}
            </Button>
          </div>
        ) : null}
        {connector === "github" ? (
          <div className="space-y-2">
            <div className="grid gap-2 md:grid-cols-3">
              <div>
                <Label htmlFor="knowledge-gh-repo">Repo (owner/name)</Label>
                <Input
                  id="knowledge-gh-repo"
                  value={githubRepo}
                  onChange={(event) => setGithubRepo(event.target.value)}
                  placeholder="acme/docs"
                />
              </div>
              <div>
                <Label htmlFor="knowledge-gh-path">File path</Label>
                <Input
                  id="knowledge-gh-path"
                  value={githubPath}
                  onChange={(event) => setGithubPath(event.target.value)}
                  placeholder="README.md"
                />
              </div>
              <div>
                <Label htmlFor="knowledge-gh-ref">Ref</Label>
                <Input
                  id="knowledge-gh-ref"
                  value={githubRef}
                  onChange={(event) => setGithubRef(event.target.value)}
                  placeholder="main"
                />
              </div>
            </div>
            <Button
              variant="accent"
              disabled={
                !githubRepo.trim() || !githubPath.trim() || uploading
              }
              onClick={() => void ingest()}
            >
              {uploading ? "Ingesting…" : "Ingest GitHub"}
            </Button>
          </div>
        ) : null}
      </section>

      <section className="rounded-xl border border-line bg-raised/40 p-3">
        <h2 className="mb-2 text-sm font-semibold">Test retrieval</h2>
        <div className="flex flex-wrap gap-2">
          <Input
            value={searchQuery}
            onChange={(event) => setSearchQuery(event.target.value)}
            placeholder="Ask a question grounded in this knowledge base"
            className="min-w-[220px] flex-1"
            onKeyDown={(event) => {
              if (event.key === "Enter") void search();
            }}
          />
          <Button
            variant="secondary"
            disabled={!searchQuery.trim() || searching}
            onClick={() => void search()}
          >
            {searching ? "Searching…" : "Search"}
          </Button>
        </div>
        {searchResults.length > 0 ? (
          <ul className="mt-2 max-h-48 space-y-1.5 overflow-y-auto">
            {searchResults.map((result) => (
              <li
                key={result.id}
                className="rounded-md border border-line px-2.5 py-2"
              >
                <p className="text-[11px] font-semibold text-teal">
                  Score {result.score.toFixed(3)}
                </p>
                <p className="mt-0.5 text-sm">{result.content}</p>
              </li>
            ))}
          </ul>
        ) : null}
      </section>

      <section className="table-shell rounded-xl">
        <div className="flex items-center justify-between border-b border-line px-4 py-2.5">
          <h2 className="text-sm font-semibold">Sources</h2>
          <Badge tone="info">{items.length}</Badge>
        </div>
        <div className="hidden grid-cols-[1.5fr_0.7fr_0.6fr_0.6fr_0.8fr] gap-3 border-b border-line px-4 py-2 md:grid">
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
              className="grid items-center gap-3 border-b border-line/60 px-4 py-2.5 last:border-0 md:grid-cols-[1.5fr_0.7fr_0.6fr_0.6fr_0.8fr]"
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
              <p className="mono-cell text-right">
                {formatBytes(source.byteSize)}
              </p>
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
              No sources yet — upload a document above.
            </li>
          ) : null}
        </ul>
      </section>
    </div>
  );
}
