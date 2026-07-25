"use client";

import Link from "next/link";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
} from "react";

import { ApprovalBanner } from "@/components/chat/ApprovalBanner";
import { MessageComposer } from "@/components/chat/MessageComposer";
import { ChatMessageList } from "@/components/chat/MessageList";
import { SessionPicker } from "@/components/chat/SessionPicker";
import {
  extractTextContent,
  isPausedRunEvent,
  isTerminalRunEvent,
  type RunEventBase,
} from "@/lib/agentos/sse";
import {
  getOrCreateGuestId,
  streamPublicAgent,
  streamPublicTeam,
  streamPublicWorkflow,
} from "@/lib/agentos/client";
import type {
  ChatMessage,
  ConversationSession,
  PublicAgentSurface,
  PublicTeamSurface,
  PublicWorkflowSurface,
} from "@/lib/api/types";

function newId(prefix: string) {
  return `${prefix}_${Math.random().toString(36).slice(2, 10)}`;
}

function sourceLabels(value: unknown): string[] {
  const labels = new Set<string>();
  const visit = (item: unknown) => {
    if (Array.isArray(item)) {
      item.forEach(visit);
    } else if (item && typeof item === "object") {
      const record = item as Record<string, unknown>;
      const metadata = (record.meta_data ?? record.metadata) as
        | Record<string, unknown>
        | undefined;
      const label = record.name ?? metadata?.filename;
      if (typeof label === "string" && label) labels.add(label);
      Object.values(record).forEach(visit);
    }
  };
  visit(value);
  return [...labels].slice(0, 6);
}

export function CustomerChat({
  surface,
  embedded = false,
}: {
  surface: PublicAgentSurface | PublicTeamSurface | PublicWorkflowSurface;
  /** Compact layout for iframe embeds (no session sidebar). */
  embedded?: boolean;
}) {
  const workflowTeams =
    "workflow" in surface ? (surface.workflow.teams ?? []) : [];
  const [runMode, setRunMode] = useState<"workflow" | "team">(
    "workflow" in surface && workflowTeams.length > 0 ? "workflow" : "workflow",
  );
  const [selectedTeamId, setSelectedTeamId] = useState<string | null>(
    workflowTeams[0]?.id ?? null,
  );
  const selectedTeam =
    workflowTeams.find((team) => team.id === selectedTeamId) ?? null;

  const target =
    "workflow" in surface
      ? surface.workflow
      : "team" in surface
        ? surface.team
        : surface.agent;
  const baseTargetType =
    "workflow" in surface ? "workflow" : "team" in surface ? "team" : "agent";
  const activeTargetType =
    baseTargetType === "workflow" && runMode === "team" && selectedTeam
      ? "team"
      : baseTargetType;
  const activeTargetName =
    activeTargetType === "team" && "workflow" in surface && selectedTeam
      ? selectedTeam.name
      : target.name;
  const activeTargetSlug =
    activeTargetType === "team" && "workflow" in surface && selectedTeam
      ? selectedTeam.slug
      : target.slug;

  const guestId = useMemo(() => getOrCreateGuestId(), []);
  const [sessions, setSessions] = useState<ConversationSession[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string>();
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [paused, setPaused] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const welcomeMessages = useCallback(
    (): ChatMessage[] => [
      {
        id: newId("welcome"),
        role: "assistant",
        content:
          activeTargetType === "team" && selectedTeam
            ? `You're chatting with ${selectedTeam.name} inside ${target.name}.`
            : target.welcomeMessage,
        createdAt: new Date().toISOString(),
        status: "complete",
      },
    ],
    [activeTargetType, selectedTeam, target.name, target.welcomeMessage],
  );

  const cssVars = useMemo(
    () =>
      ({
        "--tenant-primary": surface.tenant.primaryColor,
        "--tenant-accent": surface.tenant.accentColor,
      }) as CSSProperties,
    [surface.tenant.accentColor, surface.tenant.primaryColor],
  );

  useEffect(() => {
    const session: ConversationSession = {
      id: crypto.randomUUID(),
      title: "New conversation",
      targetType: activeTargetType,
      versionId: "",
      updatedAt: new Date().toISOString(),
      pausedForApproval: false,
      status: "active",
    };
    setSessions([session]);
    setActiveSessionId(session.id);
    setMessages(welcomeMessages());
    setPaused(false);
    setError(null);
    // Reset only when the public target changes — not when welcome text identity changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps -- intentional target switch
  }, [activeTargetSlug, activeTargetType]);

  function createSession() {
    const session: ConversationSession = {
      id: crypto.randomUUID(),
      title: "New conversation",
      targetType: activeTargetType,
      versionId: "",
      updatedAt: new Date().toISOString(),
      pausedForApproval: false,
      status: "active",
    };
    setSessions((prev) => [session, ...prev]);
    setActiveSessionId(session.id);
    setMessages(welcomeMessages());
    setPaused(false);
    setError(null);
  }

  function selectSession(sessionId: string) {
    setActiveSessionId(sessionId);
    setError(null);
    setPaused(false);
    // Guest sessions are local-only; history for prior turns is kept in-memory
    // on the active message list. Switching resets to welcome for simplicity.
    setMessages(welcomeMessages());
  }

  function removeSession(sessionId: string) {
    const remaining = sessions.filter((item) => item.id !== sessionId);
    setSessions(remaining);
    if (activeSessionId === sessionId) {
      if (remaining[0]) {
        setActiveSessionId(remaining[0].id);
        setMessages(welcomeMessages());
      } else {
        createSession();
      }
    }
  }

  async function send(text: string) {
    if (!activeSessionId) return;
    setError(null);
    setPaused(false);

    const assistantId = newId("asst");
    setMessages((prev) => [
      ...prev,
      {
        id: newId("user"),
        role: "user",
        content: text,
        createdAt: new Date().toISOString(),
        status: "complete",
      },
      {
        id: assistantId,
        role: "assistant",
        content: "",
        createdAt: new Date().toISOString(),
        status: "streaming",
      },
    ]);
    setSessions((prev) =>
      prev.map((s) =>
        s.id === activeSessionId
          ? {
              ...s,
              title:
                s.title === "Welcome" || s.title === "New conversation"
                  ? text.slice(0, 42)
                  : s.title,
              updatedAt: new Date().toISOString(),
              status: "running",
            }
          : s,
      ),
    );

    const controller = new AbortController();
    abortRef.current = controller;
    setStreaming(true);

    try {
      const runOptions = {
        tenantSlug: surface.tenant.slug,
        message: text,
        sessionId: activeSessionId,
        guestId,
        signal: controller.signal,
        onEvent: (event: RunEventBase) => {
          if (
            event.event === "RunContent" ||
            event.event === "TeamRunContent"
          ) {
            const chunk = extractTextContent(event.content);
            if (chunk) {
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === assistantId
                    ? { ...m, content: `${m.content}${chunk}` }
                    : m,
                ),
              );
            }
          }
          if (isPausedRunEvent(event)) {
            setPaused(true);
            setSessions((prev) =>
              prev.map((s) =>
                s.id === activeSessionId
                  ? {
                      ...s,
                      pausedForApproval: true,
                      status: "paused",
                      lastRunId:
                        typeof event.run_id === "string"
                          ? event.run_id
                          : s.lastRunId,
                    }
                  : s,
              ),
            );
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantId ? { ...m, status: "paused" } : m,
              ),
            );
          }
          if (event.event === "RunError") {
            setError(String(event.error ?? "Something went wrong"));
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantId ? { ...m, status: "error" } : m,
              ),
            );
            setSessions((prev) =>
              prev.map((s) =>
                s.id === activeSessionId ? { ...s, status: "error" } : s,
              ),
            );
          }
          if (isTerminalRunEvent(event) && event.event === "RunCompleted") {
            const sources = sourceLabels(event.references ?? event.citations);
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantId
                  ? {
                      ...m,
                      content:
                        sources.length > 0
                          ? `${m.content}\n\nSources: ${sources.join(", ")}`
                          : m.content,
                      status: "complete",
                    }
                  : m,
              ),
            );
            setSessions((prev) =>
              prev.map((s) =>
                s.id === activeSessionId
                  ? {
                      ...s,
                      pausedForApproval: false,
                      status: "completed",
                      lastRunId:
                        typeof event.run_id === "string"
                          ? event.run_id
                          : s.lastRunId,
                    }
                  : s,
              ),
            );
          }
        },
      };
      if (activeTargetType === "team") {
        await streamPublicTeam({
          ...runOptions,
          teamSlug: activeTargetSlug,
        });
      } else if (activeTargetType === "workflow") {
        await streamPublicWorkflow({
          ...runOptions,
          workflowSlug: activeTargetSlug,
        });
      } else {
        await streamPublicAgent({
          ...runOptions,
          agentSlug: activeTargetSlug,
        });
      }
    } catch (err) {
      if (controller.signal.aborted) {
        setMessages((prev) =>
          prev.map((m) =>
            m.id === assistantId ? { ...m, status: "cancelled" } : m,
          ),
        );
      } else {
        setMessages((prev) =>
          prev.map((m) =>
            m.id === assistantId
              ? {
                  ...m,
                  content: m.content || "The run could not be completed.",
                  status: "error",
                }
              : m,
          ),
        );
        setError(err instanceof Error ? err.message : "Stream failed");
      }
    } finally {
      setStreaming(false);
      abortRef.current = null;
    }
  }

  const shellClass = embedded
    ? "min-h-[100dvh] text-white"
    : "min-h-screen text-white";

  return (
    <div style={cssVars} data-theme="dark" className={shellClass}>
      <div
        className={`relative overflow-hidden ${embedded ? "min-h-[100dvh]" : "min-h-screen"}`}
        style={{
          background: `
            radial-gradient(1000px 500px at 15% 0%, color-mix(in oklab, var(--tenant-accent) 35%, transparent), transparent 55%),
            linear-gradient(160deg, var(--tenant-primary) 0%, #04110c 55%, #020807 100%)
          `,
        }}
      >
        <div className="pointer-events-none absolute inset-0 opacity-[0.18] grid-noise" />
        <div
          className={`relative mx-auto grid gap-0 ${
            embedded
              ? "min-h-[100dvh] max-w-none"
              : "min-h-screen max-w-6xl lg:grid-cols-[240px_minmax(0,1fr)]"
          }`}
        >
          {!embedded ? (
            <aside className="border-b border-white/10 p-5 lg:border-b-0 lg:border-r">
              <p className="font-display text-3xl font-semibold tracking-tight">
                {surface.tenant.name}
              </p>
              {surface.tenant.tagline ? (
                <p className="mt-2 text-sm text-white/65">
                  {surface.tenant.tagline}
                </p>
              ) : null}
              <div className="mt-8">
                <SessionPicker
                  sessions={sessions}
                  activeId={activeSessionId}
                  onSelect={(id) => selectSession(id)}
                  onCreate={createSession}
                  onDelete={(id) => removeSession(id)}
                />
              </div>
            </aside>
          ) : null}

          <section
            className={`flex flex-col ${embedded ? "min-h-[100dvh]" : "min-h-[70vh]"}`}
          >
            <header className="border-b border-white/10 px-5 py-4">
              <div className="flex items-center justify-between gap-3">
                <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-[var(--tenant-accent)]">
                  {embedded ? surface.tenant.name : target.slug}
                </p>
                {!embedded && baseTargetType === "workflow" ? (
                  <Link
                    href={`/t/${surface.tenant.slug}/chat`}
                    className="rounded-lg border border-white/15 px-3 py-1.5 text-xs font-medium text-white/70 transition hover:border-[var(--tenant-accent)]/60 hover:text-white"
                  >
                    Switch workflow
                  </Link>
                ) : null}
              </div>
              <h1
                className={`mt-1 font-display font-semibold tracking-tight ${
                  embedded ? "text-xl" : "text-3xl"
                }`}
              >
                {activeTargetName}
              </h1>
              {!embedded ? (
                <p className="mt-1 max-w-2xl text-sm text-white/65">
                  {target.description}
                </p>
              ) : null}
              {baseTargetType === "workflow" && workflowTeams.length > 0 ? (
                <div className="mt-4 flex flex-wrap gap-2">
                  <button
                    type="button"
                    onClick={() => {
                      setRunMode("workflow");
                    }}
                    className={`rounded-lg border px-3 py-1.5 text-xs font-medium transition ${
                      runMode === "workflow"
                        ? "border-[var(--tenant-accent)] bg-white/10 text-white"
                        : "border-white/15 text-white/65 hover:border-white/30 hover:text-white"
                    }`}
                  >
                    Full workflow
                  </button>
                  {workflowTeams.map((team) => (
                    <button
                      key={team.id}
                      type="button"
                      onClick={() => {
                        setRunMode("team");
                        setSelectedTeamId(team.id);
                      }}
                      className={`rounded-lg border px-3 py-1.5 text-xs font-medium transition ${
                        runMode === "team" && selectedTeamId === team.id
                          ? "border-[var(--tenant-accent)] bg-white/10 text-white"
                          : "border-white/15 text-white/65 hover:border-white/30 hover:text-white"
                      }`}
                    >
                      {team.stepName || team.name}
                    </button>
                  ))}
                </div>
              ) : null}
            </header>
            <ApprovalBanner visible={paused} />
            <ChatMessageList messages={messages} dark />
            {error ? (
              <p className="px-5 py-2 text-xs text-amber">{error}</p>
            ) : null}
            <MessageComposer
              dark
              disabled={streaming || paused || !activeSessionId}
              streaming={streaming}
              onSend={send}
              onCancel={() => abortRef.current?.abort()}
              placeholder={`Message ${activeTargetName}…`}
            />
          </section>
        </div>
      </div>
    </div>
  );
}
