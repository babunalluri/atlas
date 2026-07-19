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
  deleteChatSession,
  getChatSession,
  listChatSessions,
  streamConfiguredAgent,
  streamConfiguredTeam,
  streamConfiguredWorkflow,
} from "@/lib/agentos/client";
import type {
  ChatMessage,
  ConversationSession,
  PublicAgentSurface,
  PublicTeamSurface,
  PublicWorkflowSurface,
} from "@/lib/api/types";
import { useAgentOsToken } from "@/lib/auth/token";

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
}: {
  surface: PublicAgentSurface | PublicTeamSurface | PublicWorkflowSurface;
}) {
  const target =
    "workflow" in surface
      ? surface.workflow
      : "team" in surface
        ? surface.team
        : surface.agent;
  const targetType =
    "workflow" in surface ? "workflow" : "team" in surface ? "team" : "agent";
  const { getAccessToken, isSignedIn } = useAgentOsToken();
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
        content: target.welcomeMessage,
        createdAt: new Date().toISOString(),
        status: "complete",
      },
    ],
    [target.welcomeMessage],
  );

  const cssVars = useMemo(
    () =>
      ({
        "--tenant-primary": surface.tenant.primaryColor,
        "--tenant-accent": surface.tenant.accentColor,
      }) as CSSProperties,
    [surface.tenant.accentColor, surface.tenant.primaryColor],
  );

  const loadSession = useCallback(
    async (sessionId: string) => {
      const token = await getAccessToken();
      const detail = await getChatSession(token, sessionId);
      setSessions((current) =>
        current.map((item) =>
          item.id === detail.session.id ? detail.session : item,
        ),
      );
      setMessages(
        detail.messages.length > 0 ? detail.messages : welcomeMessages(),
      );
      setPaused(detail.session.pausedForApproval);
      return detail.session;
    },
    [getAccessToken, welcomeMessages],
  );

  useEffect(() => {
    if (!isSignedIn) return;
    let cancelled = false;
    void (async () => {
      try {
        const rows = await listChatSessions(
          await getAccessToken(),
          targetType,
          target.id,
        );
        if (cancelled) return;
        setSessions(rows);
        if (rows[0]) {
          setActiveSessionId(rows[0].id);
          await loadSession(rows[0].id);
        } else {
          setMessages(welcomeMessages());
        }
      } catch (reason) {
        if (!cancelled) {
          setError(
            reason instanceof Error ? reason.message : "Could not load sessions",
          );
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [
    getAccessToken,
    isSignedIn,
    loadSession,
    target.id,
    targetType,
    welcomeMessages,
  ]);

  useEffect(() => {
    if (!paused || !activeSessionId || !isSignedIn) return;
    const interval = window.setInterval(() => {
      void loadSession(activeSessionId)
        .then((session) => {
          if (!session.pausedForApproval) {
            window.clearInterval(interval);
          }
        })
        .catch(() => undefined);
    }, 3000);
    return () => window.clearInterval(interval);
  }, [activeSessionId, isSignedIn, loadSession, paused]);

  function createSession() {
    const session: ConversationSession = {
      id: crypto.randomUUID(),
      title: "New conversation",
      targetType,
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

  async function selectSession(sessionId: string) {
    setActiveSessionId(sessionId);
    setError(null);
    try {
      await loadSession(sessionId);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not load session");
    }
  }

  async function removeSession(sessionId: string) {
    try {
      await deleteChatSession(await getAccessToken(), sessionId);
      const remaining = sessions.filter((item) => item.id !== sessionId);
      setSessions(remaining);
      if (activeSessionId === sessionId) {
        setActiveSessionId(remaining[0]?.id);
        if (remaining[0]) {
          await loadSession(remaining[0].id);
        } else {
          setMessages(welcomeMessages());
        }
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not delete session");
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
              title: s.title === "Welcome" || s.title === "New conversation" ? text.slice(0, 42) : s.title,
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
      if (!isSignedIn) {
        throw new Error("Sign in required to chat with this agent");
      }
      const token = await getAccessToken();
      const runOptions = {
        accessToken: token,
        message: text,
        sessionId: activeSessionId,
        preview: false,
        signal: controller.signal,
        onEvent: (event: RunEventBase) => {
          if (event.event === "RunContent") {
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
      if (targetType === "team") {
        await streamConfiguredTeam({ ...runOptions, teamConfigId: target.id });
      } else if (targetType === "workflow") {
        await streamConfiguredWorkflow({
          ...runOptions,
          workflowConfigId: target.id,
        });
      } else {
        await streamConfiguredAgent({ ...runOptions, agentConfigId: target.id });
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

  return (
    <div style={cssVars} data-theme="dark" className="min-h-screen text-white">
      <div
        className="relative min-h-screen overflow-hidden"
        style={{
          background: `
            radial-gradient(1000px 500px at 15% 0%, color-mix(in oklab, var(--tenant-accent) 35%, transparent), transparent 55%),
            linear-gradient(160deg, var(--tenant-primary) 0%, #04110c 55%, #020807 100%)
          `,
        }}
      >
        <div className="pointer-events-none absolute inset-0 opacity-[0.18] grid-noise" />
        <div className="relative mx-auto grid min-h-screen max-w-6xl gap-0 lg:grid-cols-[240px_minmax(0,1fr)]">
          <aside className="border-b border-white/10 p-5 lg:border-b-0 lg:border-r">
            <p className="font-display text-3xl font-semibold tracking-tight">
              {surface.tenant.name}
            </p>
            {surface.tenant.tagline ? (
              <p className="mt-2 text-sm text-white/65">{surface.tenant.tagline}</p>
            ) : null}
            <div className="mt-8">
              <SessionPicker
                sessions={sessions}
                activeId={activeSessionId}
                onSelect={(id) => void selectSession(id)}
                onCreate={createSession}
                onDelete={(id) => void removeSession(id)}
              />
            </div>
          </aside>

          <section className="flex min-h-[70vh] flex-col">
            <header className="border-b border-white/10 px-5 py-5">
              <div className="flex items-center justify-between gap-3">
                <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-[var(--tenant-accent)]">
                  {target.slug}
                </p>
                {targetType === "workflow" ? (
                  <Link
                    href={`/t/${surface.tenant.slug}/chat`}
                    className="rounded-lg border border-white/15 px-3 py-1.5 text-xs font-medium text-white/70 transition hover:border-[var(--tenant-accent)]/60 hover:text-white"
                  >
                    Switch workflow
                  </Link>
                ) : null}
              </div>
              <h1 className="mt-1 font-display text-3xl font-semibold tracking-tight">
                {target.name}
              </h1>
              <p className="mt-1 max-w-2xl text-sm text-white/65">
                {target.description}
              </p>
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
              placeholder={`Message ${target.name}…`}
            />
          </section>
        </div>
      </div>
    </div>
  );
}
