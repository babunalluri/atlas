"use client";

import { useState } from "react";

import { Button } from "@/components/ui/Button";
import { Input, Label, Textarea } from "@/components/ui/Field";
import {
  createUserMemory,
  deleteUserMemory,
  listUserMemories,
  optimizeUserMemories,
  updateUserMemory,
} from "@/lib/api/admin";
import type { UserMemory } from "@/lib/api/types";
import { useAgentOsToken } from "@/lib/auth/token";

export function MemoriesPanel({
  initialUserId = "",
}: {
  initialUserId?: string;
}) {
  const { getAccessToken } = useAgentOsToken();
  const [userId, setUserId] = useState(initialUserId);
  const [memories, setMemories] = useState<UserMemory[]>([]);
  const [draft, setDraft] = useState("");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function refresh(targetUserId = userId) {
    if (!targetUserId.trim()) {
      setError("Enter a Clerk user id to load memories.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      setMemories(await listUserMemories(await getAccessToken(), targetUserId.trim()));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Failed to load memories");
    } finally {
      setBusy(false);
    }
  }

  async function save() {
    if (!userId.trim() || !draft.trim()) {
      setError("User id and memory text are required.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const token = await getAccessToken();
      if (editingId) {
        await updateUserMemory(token, editingId, {
          userId: userId.trim(),
          memory: draft.trim(),
        });
      } else {
        await createUserMemory(token, {
          userId: userId.trim(),
          memory: draft.trim(),
        });
      }
      setDraft("");
      setEditingId(null);
      await refresh(userId.trim());
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Save failed");
      setBusy(false);
    }
  }

  async function optimize() {
    if (!userId.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const result = await optimizeUserMemories(
        await getAccessToken(),
        userId.trim(),
        true,
      );
      setMemories(result.memories);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Optimize failed");
    } finally {
      setBusy(false);
    }
  }

  async function remove(memory: UserMemory) {
    setBusy(true);
    setError(null);
    try {
      await deleteUserMemory(await getAccessToken(), memory.id, userId.trim());
      setMemories((current) => current.filter((item) => item.id !== memory.id));
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
          Memories
        </h1>
        <p className="mt-2 max-w-2xl text-sm text-slate-muted">
          Tenant-namespaced user memories. Create, edit, and summarize
          persistent memory for people in this workspace.
        </p>
      </header>

      <section className="grid gap-3 rounded-xl border border-line bg-raised/50 p-4 md:grid-cols-[1.2fr_auto_auto]">
        <div>
          <Label htmlFor="memory-user">User id</Label>
          <Input
            id="memory-user"
            value={userId}
            placeholder="user_..."
            onChange={(event) => setUserId(event.target.value)}
          />
        </div>
        <div className="flex items-end">
          <Button disabled={busy} onClick={() => refresh()}>
            {busy ? "Loading…" : "Load"}
          </Button>
        </div>
        <div className="flex items-end">
          <Button variant="secondary" disabled={busy || !userId.trim()} onClick={optimize}>
            Optimize
          </Button>
        </div>
      </section>

      <section className="grid gap-3 rounded-xl border border-line bg-raised/50 p-4">
        <Label htmlFor="memory-draft">
          {editingId ? "Update memory" : "New memory"}
        </Label>
        <Textarea
          id="memory-draft"
          rows={3}
          value={draft}
          placeholder="User prefers concise answers…"
          onChange={(event) => setDraft(event.target.value)}
        />
        <div className="flex justify-end gap-2">
          {editingId ? (
            <Button
              variant="secondary"
              onClick={() => {
                setEditingId(null);
                setDraft("");
              }}
            >
              Cancel
            </Button>
          ) : null}
          <Button variant="accent" disabled={busy} onClick={save}>
            {editingId ? "Update" : "Create"}
          </Button>
        </div>
      </section>

      {error ? <p className="text-sm text-rose">{error}</p> : null}

      <section className="table-shell rounded-xl">
        {memories.map((memory) => (
          <div
            key={memory.id}
            className="flex flex-col gap-2 border-b border-line/60 px-4 py-3 last:border-0 md:flex-row md:items-start md:justify-between"
          >
            <div className="min-w-0">
              <p className="text-sm">{memory.memory}</p>
              <p className="mono-cell mt-1 text-slate-muted">{memory.id}</p>
            </div>
            <div className="flex gap-2">
              <Button
                variant="secondary"
                onClick={() => {
                  setEditingId(memory.id);
                  setDraft(memory.memory);
                }}
              >
                Edit
              </Button>
              <Button variant="secondary" onClick={() => remove(memory)}>
                Delete
              </Button>
            </div>
          </div>
        ))}
        {memories.length === 0 ? (
          <p className="px-4 py-10 text-center text-sm text-slate-muted">
            No memories loaded yet.
          </p>
        ) : null}
      </section>
    </div>
  );
}
