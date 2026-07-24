"use client";

import { useMemo, useRef, useState } from "react";

import { Button } from "@/components/ui/Button";
import { Input, Label } from "@/components/ui/Field";
import { extractTextContent, parseSseChunk } from "@/lib/agentos/sse";
import type { PublicApiCatalogRow } from "@/lib/api/public-api-catalog";
import { cn } from "@/lib/utils";

type RunMode = "first" | "continue" | "new";

function apiBase(): string {
  return (
    process.env.NEXT_PUBLIC_AGENTOS_URL?.replace(/\/$/, "") ??
    "http://localhost:7777"
  );
}

function shellSingleQuote(value: string): string {
  return `'${value.replace(/'/g, `'\\''`)}'`;
}

export function PublicApiTestBed({
  catalog,
  catalogLoading = false,
  emptyHint = null,
  workflowId,
  teamId,
  onWorkflowChange,
  onTeamChange,
}: {
  catalog: PublicApiCatalogRow[];
  catalogLoading?: boolean;
  emptyHint?: string | null;
  workflowId: string;
  teamId: string;
  onWorkflowChange: (id: string) => void;
  onTeamChange: (id: string) => void;
}) {
  const [secret, setSecret] = useState("");
  const [mode, setMode] = useState<RunMode>("first");
  const [sessionId, setSessionId] = useState("");
  const [message, setMessage] = useState("Hello");
  const [streaming, setStreaming] = useState(false);
  const [output, setOutput] = useState("");
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showCurl, setShowCurl] = useState(false);
  const [copiedCurl, setCopiedCurl] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  const selected = catalog.find((row) => row.workflow.id === workflowId);

  const curlCommand = useMemo(() => {
    const secretValue = secret.trim() || "<SERVICE_ACCOUNT_TOKEN>";
    const wf = workflowId || "<WORKFLOW_ID>";
    const team = teamId || "<TEAM_ID>";
    const msg = message.trim() || "Hello";
    const lines = [
      `curl -N -D - -X POST ${shellSingleQuote(`${apiBase()}/api/v1/public/runs`)} \\`,
      `  -H ${shellSingleQuote(`Authorization: Bearer ${secretValue}`)} \\`,
      `  -F ${shellSingleQuote(`workflow_id=${wf}`)} \\`,
      `  -F ${shellSingleQuote(`team_id=${team}`)} \\`,
    ];
    if (mode === "continue") {
      lines.push(
        `  -F ${shellSingleQuote(`session_id=${sessionId.trim() || "<SESSION_ID>"}`)} \\`,
      );
    }
    if (mode === "new") {
      lines.push(`  -F ${shellSingleQuote("new_session=true")} \\`);
    }
    lines.push(`  -F ${shellSingleQuote(`message=${msg}`)}`);
    return lines.join("\n");
  }, [message, mode, secret, sessionId, teamId, workflowId]);

  async function copyCurl() {
    await navigator.clipboard.writeText(curlCommand);
    setCopiedCurl(true);
    window.setTimeout(() => setCopiedCurl(false), 1500);
  }

  async function run() {
    if (!secret.trim()) {
      setError("Paste a service account token first.");
      return;
    }
    if (!workflowId || !teamId) {
      setError("Pick a workflow and team.");
      return;
    }
    if (!message.trim()) {
      setError("Enter a message.");
      return;
    }
    if (mode === "continue" && !sessionId.trim()) {
      setError("Send a first message before continuing.");
      return;
    }

    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setStreaming(true);
    setError(null);
    setOutput("");
    setStatus(
      mode === "first"
        ? "Starting new chat…"
        : mode === "new"
          ? "Starting new session…"
          : "Continuing…",
    );

    const body = new FormData();
    body.set("workflow_id", workflowId);
    body.set("team_id", teamId);
    body.set("message", message.trim());
    if (mode === "continue") body.set("session_id", sessionId.trim());
    if (mode === "new") body.set("new_session", "true");

    try {
      const response = await fetch(`${apiBase()}/api/v1/public/runs`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${secret.trim()}`,
          Accept: "text/event-stream",
        },
        body,
        signal: controller.signal,
      });

      const headerSession = response.headers.get("X-Session-Id");
      if (headerSession) setSessionId(headerSession);

      if (!response.ok) {
        const text = await response.text().catch(() => "");
        throw new Error(
          `HTTP ${response.status}: ${text || response.statusText}`,
        );
      }
      if (!response.body) throw new Error("No response stream");

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let remainder = "";
      let assistant = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        const parsed = parseSseChunk(
          decoder.decode(value, { stream: true }),
          remainder,
        );
        remainder = parsed.remainder;
        for (const frame of parsed.frames) {
          let payload: Record<string, unknown>;
          try {
            payload = JSON.parse(frame.data) as Record<string, unknown>;
          } catch {
            continue;
          }
          const eventName = String(payload.event ?? "");
          if (eventName === "SessionStarted") {
            const sid = String(payload.session_id ?? "");
            if (sid) {
              setSessionId(sid);
              setStatus(`Session ${sid.slice(0, 8)}…`);
            }
          } else if (eventName === "RunContent") {
            const piece = extractTextContent(payload.content);
            if (piece) {
              assistant += piece;
              setOutput(assistant);
            }
          } else if (eventName === "RunError") {
            throw new Error(String(payload.error ?? "Run failed"));
          } else if (eventName === "RunCompleted") {
            setStatus("Done");
          }
        }
      }
      setMode("continue");
    } catch (reason) {
      if (!controller.signal.aborted) {
        setError(
          reason instanceof Error ? reason.message : "Public API run failed",
        );
        setStatus(null);
      }
    } finally {
      setStreaming(false);
      abortRef.current = null;
    }
  }

  return (
    <section className="table-shell space-y-3 rounded-xl p-4">
      <h2 className="text-sm font-semibold">Try it</h2>

      <div className="grid gap-3 sm:grid-cols-2">
        <div className="sm:col-span-2">
          <Label htmlFor="testbed-secret">Service account token</Label>
          <Input
            id="testbed-secret"
            type="password"
            autoComplete="off"
            value={secret}
            onChange={(e) => setSecret(e.target.value)}
            placeholder="Paste token from Service accounts"
          />
        </div>
        <div>
          <Label htmlFor="testbed-workflow">Workflow</Label>
          <select
            id="testbed-workflow"
            className="mt-1 w-full rounded-md border border-line bg-raised px-3 py-2 text-sm"
            value={workflowId}
            disabled={catalogLoading || catalog.length === 0}
            onChange={(e) => {
              const next = e.target.value;
              onWorkflowChange(next);
              const row = catalog.find((item) => item.workflow.id === next);
              onTeamChange(row?.teams[0]?.id ?? "");
            }}
          >
            {catalogLoading ? (
              <option value="">Loading workflows…</option>
            ) : catalog.length === 0 ? (
              <option value="">No runnable workflows</option>
            ) : (
              catalog.map((row) => (
                <option key={row.workflow.id} value={row.workflow.id}>
                  {row.workflow.name}
                </option>
              ))
            )}
          </select>
        </div>
        <div>
          <Label htmlFor="testbed-team">Team</Label>
          <select
            id="testbed-team"
            className="mt-1 w-full rounded-md border border-line bg-raised px-3 py-2 text-sm"
            value={teamId}
            onChange={(e) => onTeamChange(e.target.value)}
            disabled={catalogLoading || !selected?.teams.length}
          >
            {catalogLoading ? (
              <option value="">Loading teams…</option>
            ) : (selected?.teams ?? []).length === 0 ? (
              <option value="">No team steps</option>
            ) : (
              (selected?.teams ?? []).map((team) => (
                <option key={team.id} value={team.id}>
                  {team.stepName}: {team.name}
                </option>
              ))
            )}
          </select>
        </div>
      </div>
      {!catalogLoading && catalog.length === 0 && emptyHint ? (
        <p className="text-sm text-slate-muted">{emptyHint}</p>
      ) : null}

      <div className="flex flex-wrap gap-1">
        {(
          [
            ["first", "New chat"],
            ["continue", "Continue"],
            ["new", "Reset chat"],
          ] as const
        ).map(([value, label]) => (
          <button
            key={value}
            type="button"
            onClick={() => setMode(value)}
            className={cn(
              "rounded-md px-2.5 py-1.5 text-xs font-medium",
              mode === value
                ? "bg-ink text-canvas"
                : "bg-raised text-slate-muted hover:bg-mist",
            )}
          >
            {label}
          </button>
        ))}
        {sessionId ? (
          <span className="ml-auto self-center mono-cell text-slate-muted">
            session {sessionId.slice(0, 8)}…
          </span>
        ) : null}
      </div>

      <div className="flex gap-2">
        <Input
          value={message}
          onChange={(e) => setMessage(e.target.value)}
          placeholder="Message"
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              void run();
            }
          }}
        />
        <Button
          variant="accent"
          disabled={streaming || catalogLoading || catalog.length === 0}
          onClick={() => void run()}
        >
          {streaming ? "…" : "Send"}
        </Button>
      </div>

      {error ? <p className="text-sm text-rose">{error}</p> : null}
      {status ? <p className="text-xs text-slate-muted">{status}</p> : null}

      <div className="min-h-[140px] rounded-lg border border-line bg-[#071018] p-3 font-mono text-xs leading-relaxed text-[#d7e0e8]">
        {output || (
          <span className="text-white/40">Reply appears here.</span>
        )}
      </div>

      <div className="flex flex-wrap gap-2">
        <Button
          size="sm"
          variant="secondary"
          onClick={() => setShowCurl((open) => !open)}
        >
          {showCurl ? "Hide cURL" : "Show cURL"}
        </Button>
        {showCurl ? (
          <Button size="sm" variant="secondary" onClick={() => void copyCurl()}>
            {copiedCurl ? "Copied" : "Copy cURL"}
          </Button>
        ) : null}
        {streaming ? (
          <Button
            size="sm"
            variant="ghost"
            onClick={() => abortRef.current?.abort()}
          >
            Cancel
          </Button>
        ) : null}
      </div>
      {showCurl ? (
        <pre className="overflow-x-auto rounded-lg bg-[#071018] p-3 font-mono text-[11px] text-[#d7e0e8]">
          {curlCommand}
        </pre>
      ) : null}
    </section>
  );
}
