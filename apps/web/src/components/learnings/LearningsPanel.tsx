"use client";

import { useEffect, useState } from "react";

import { Button } from "@/components/ui/Button";
import { Input, Label, Select, Textarea } from "@/components/ui/Field";
import {
  createLearning,
  deleteLearning,
  listLearnings,
  type LearningRecord,
} from "@/lib/api/admin";
import { useAgentOsToken } from "@/lib/auth/token";

export function LearningsPanel() {
  const { getAccessToken } = useAgentOsToken();
  const [rows, setRows] = useState<LearningRecord[]>([]);
  const [learningType, setLearningType] = useState("user_memory");
  const [userId, setUserId] = useState("");
  const [content, setContent] = useState('{"notes":""}');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function refresh() {
    setBusy(true);
    setError(null);
    try {
      setRows(
        await listLearnings(await getAccessToken(), {
          userId: userId.trim() || undefined,
          learningType: learningType || undefined,
        }),
      );
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Failed to load learnings");
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    void refresh();
    // Initial load only.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function create() {
    let parsed: Record<string, unknown>;
    try {
      parsed = JSON.parse(content) as Record<string, unknown>;
    } catch {
      setError("Content must be valid JSON.");
      return;
    }
    if (
      ["user_profile", "user_memory", "entity_memory", "session_context"].includes(
        learningType,
      ) &&
      !userId.trim()
    ) {
      setError("Identity-keyed learnings need a user id.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await createLearning(await getAccessToken(), {
        learningType,
        content: parsed,
        userId: userId.trim() || undefined,
      });
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Create failed");
      setBusy(false);
    }
  }

  async function remove(row: LearningRecord) {
    setBusy(true);
    setError(null);
    try {
      await deleteLearning(await getAccessToken(), row.learningId);
      setRows((current) =>
        current.filter((item) => item.learningId !== row.learningId),
      );
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Delete failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-6">
      <header>
        <p className="text-xs font-semibold uppercase tracking-[0.16em] text-teal">
          Configure
        </p>
        <h1 className="mt-2 font-display text-4xl font-semibold tracking-tight">
          Learnings
        </h1>
        <p className="mt-2 max-w-2xl text-sm text-slate-muted">
          Learning records under tenant runtime user namespaces.
        </p>
      </header>

      <section className="grid gap-3 rounded-xl border border-line bg-raised/50 p-4 md:grid-cols-2">
        <div>
          <Label htmlFor="learning-type">Learning type</Label>
          <Select
            id="learning-type"
            value={learningType}
            onChange={(event) => setLearningType(event.target.value)}
          >
            <option value="user_memory">user_memory</option>
            <option value="user_profile">user_profile</option>
            <option value="session_context">session_context</option>
            <option value="entity_memory">entity_memory</option>
            <option value="decision_log">decision_log</option>
            <option value="learned_knowledge">learned_knowledge</option>
          </Select>
        </div>
        <div>
          <Label htmlFor="learning-user">User id (optional filter / owner)</Label>
          <Input
            id="learning-user"
            value={userId}
            placeholder="user_..."
            onChange={(event) => setUserId(event.target.value)}
          />
        </div>
        <div className="md:col-span-2">
          <Label htmlFor="learning-content">Content JSON</Label>
          <Textarea
            id="learning-content"
            rows={4}
            value={content}
            onChange={(event) => setContent(event.target.value)}
            className="font-mono text-[13px]"
          />
        </div>
        <div className="flex gap-2 md:col-span-2">
          <Button disabled={busy} onClick={() => refresh()}>
            Refresh
          </Button>
          <Button variant="accent" disabled={busy} onClick={create}>
            Create learning
          </Button>
        </div>
      </section>

      {error ? <p className="text-sm text-rose">{error}</p> : null}

      <section className="table-shell rounded-xl">
        {rows.map((row) => (
          <div
            key={row.learningId}
            className="grid gap-2 border-b border-line/60 px-4 py-3 last:border-0 md:grid-cols-[1fr_auto]"
          >
            <div className="min-w-0">
              <p className="text-sm font-medium">
                {row.learningType}
                {row.userId ? ` · ${row.userId}` : ""}
              </p>
              <p className="mono-cell mt-1 truncate text-slate-muted">
                {row.learningId}
              </p>
              <pre className="mt-2 overflow-x-auto rounded-md bg-canvas/60 p-2 text-xs">
                {JSON.stringify(row.content, null, 2)}
              </pre>
            </div>
            <div className="flex items-start">
              <Button variant="secondary" onClick={() => remove(row)}>
                Delete
              </Button>
            </div>
          </div>
        ))}
        {rows.length === 0 ? (
          <p className="px-4 py-10 text-center text-sm text-slate-muted">
            No learnings yet.
          </p>
        ) : null}
      </section>
    </div>
  );
}
