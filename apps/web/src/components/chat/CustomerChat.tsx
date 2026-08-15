"use client";

import type { Session } from "next-auth";
import { useTranslations } from "next-intl";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
} from "react";

import { ApprovalBanner } from "@/components/chat/ApprovalBanner";
import { ChatAccountBar } from "@/components/chat/ChatAccountBar";
import { MessageComposer } from "@/components/chat/MessageComposer";
import { ChatMessageList } from "@/components/chat/MessageList";
import { SessionPicker } from "@/components/chat/SessionPicker";
import { NotificationBell } from "@/components/notifications/NotificationBell";
import {
  ThemeToggle,
  useSurfaceTheme,
} from "@/components/layout/ThemeToggle";
import {
  extractTextContent,
  isPausedRunEvent,
  isTerminalRunEvent,
  type RunEventBase,
} from "@/lib/agentos/sse";
import {
  cancelConfiguredRun,
  formatApiError,
  resumeConfiguredRun,
  streamConfiguredTeam,
  streamConfiguredWorkflow,
} from "@/lib/agentos/client";
import type {
  ChatMessage,
  ConversationSession,
  PublicTeamSurface,
  PublicWorkflowSurface,
} from "@/lib/api/types";
import { useAgentOsToken } from "@/lib/auth/token";
import { Link, useRouter } from "@/i18n/navigation";

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
  serverSession = null,
}: {
  surface: PublicTeamSurface | PublicWorkflowSurface;
  /** Compact layout for iframe embeds (no session sidebar). */
  embedded?: boolean;
  serverSession?: Session | null;
}) {
  const tCommon = useTranslations("common");
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
    "workflow" in surface ? surface.workflow : surface.team;
  const baseTargetType = "workflow" in surface ? "workflow" : "team";
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

  const router = useRouter();
  const { getAccessToken, isLoaded, isSignedIn } = useAgentOsToken();
  const { theme, dark, changeTheme } = useSurfaceTheme("workspace");
  const [authReady, setAuthReady] = useState(false);
  // Org-only product: hosted and embed chat require Clerk sign-in.
  const useStaffAuth = isLoaded && isSignedIn;
  const [sessions, setSessions] = useState<ConversationSession[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string>();
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [paused, setPaused] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const runIdRef = useRef<string | null>(null);
  const lastEventIdRef = useRef<string | undefined>(undefined);
  const cancelRequestedRef = useRef(false);

  useEffect(() => {
    if (!isLoaded) return;
    if (!isSignedIn) {
      const next =
        typeof window !== "undefined"
          ? `${window.location.pathname}${window.location.search}`
          : `/t/${surface.tenant.slug}/chat`;
      router.replace(`/sign-in?redirect_url=${encodeURIComponent(next)}`);
      return;
    }
    setAuthReady(true);
  }, [isLoaded, isSignedIn, router, surface.tenant.slug]);

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

  function abortActiveStream() {
    cancelRequestedRef.current = true;
    abortRef.current?.abort();
    abortRef.current = null;
    setStreaming(false);
  }

  function resolveStaffTeamConfigId(): string | null {
    if (activeTargetType !== "team") return null;
    if ("workflow" in surface && selectedTeam && runMode === "team") {
      return selectedTeam.id;
    }
    if ("team" in surface) return surface.team.id;
    return null;
  }

  async function cancelActiveRun() {
    cancelRequestedRef.current = true;
    const runId = runIdRef.current;
    const sessionId = activeSessionId;
    if (runId && sessionId && useStaffAuth) {
      try {
        const accessToken = await getAccessToken();
        if (activeTargetType === "team") {
          const configId = resolveStaffTeamConfigId();
          if (configId) {
            await cancelConfiguredRun({
              accessToken,
              kind: "team",
              runId,
              sessionId,
              configId,
            });
          }
        } else if ("workflow" in surface) {
          await cancelConfiguredRun({
            accessToken,
            kind: "workflow",
            runId,
            sessionId,
            configId: surface.workflow.id,
          });
        }
      } catch {
        // Best-effort: still abort the local stream.
      }
    }
    abortRef.current?.abort();
    abortRef.current = null;
    setStreaming(false);
  }

  useEffect(() => {
    return () => {
      abortRef.current?.abort();
      abortRef.current = null;
    };
  }, []);

  useEffect(() => {
    abortActiveStream();
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
    abortActiveStream();
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
    abortActiveStream();
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
    if (!activeSessionId || !useStaffAuth) return;
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
    cancelRequestedRef.current = false;
    runIdRef.current = null;
    lastEventIdRef.current = undefined;
    setStreaming(true);

    const trackEvent = (event: RunEventBase, frame?: { id?: string }) => {
      if (typeof event.run_id === "string") {
        runIdRef.current = event.run_id;
      }
      if (frame?.id) {
        lastEventIdRef.current = frame.id;
      }
    };

    try {
      const onEvent = (event: RunEventBase, frame?: { id?: string }) => {
        trackEvent(event, frame);
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
      };

      const streamOpts = {
        message: text,
        sessionId: activeSessionId,
        signal: controller.signal,
        onEvent,
      };

      let streamResult: { lastEventId?: string };
      const accessToken = await getAccessToken();
      if (activeTargetType === "team") {
        const configId = resolveStaffTeamConfigId();
        if (!configId) {
          throw new Error("Team id is missing for authenticated run");
        }
        streamResult = await streamConfiguredTeam({
          ...streamOpts,
          accessToken,
          teamConfigId: configId,
          preview: false,
        });
      } else if ("workflow" in surface) {
        streamResult = await streamConfiguredWorkflow({
          ...streamOpts,
          accessToken,
          workflowConfigId: surface.workflow.id,
          preview: false,
        });
      } else {
        throw new Error("Workflow id is missing for authenticated run");
      }
      if (streamResult.lastEventId) {
        lastEventIdRef.current = streamResult.lastEventId;
      }

      if (controller.signal.aborted || cancelRequestedRef.current) {
        setMessages((prev) =>
          prev.map((m) =>
            m.id === assistantId ? { ...m, status: "cancelled" } : m,
          ),
        );
      }
    } catch (err) {
      if (controller.signal.aborted || cancelRequestedRef.current) {
        setMessages((prev) =>
          prev.map((m) =>
            m.id === assistantId ? { ...m, status: "cancelled" } : m,
          ),
        );
      } else if (
        runIdRef.current &&
        activeSessionId &&
        !cancelRequestedRef.current
      ) {
        // Network/disconnect: one resume attempt with Last-Event-ID catch-up.
        try {
          const resumeController = new AbortController();
          abortRef.current = resumeController;
          const resumeOnEvent = (event: RunEventBase, frame?: { id?: string }) => {
            trackEvent(event, frame);
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
            if (isTerminalRunEvent(event) && event.event === "RunCompleted") {
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === assistantId ? { ...m, status: "complete" } : m,
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
            }
          };
          const accessToken = await getAccessToken();
          if (activeTargetType === "team") {
            const configId = resolveStaffTeamConfigId();
            if (!configId) throw err;
            await resumeConfiguredRun({
              accessToken,
              kind: "team",
              runId: runIdRef.current,
              sessionId: activeSessionId,
              configId,
              lastEventId: lastEventIdRef.current,
              signal: resumeController.signal,
              onEvent: resumeOnEvent,
            });
          } else if ("workflow" in surface) {
            await resumeConfiguredRun({
              accessToken,
              kind: "workflow",
              runId: runIdRef.current,
              sessionId: activeSessionId,
              configId: surface.workflow.id,
              lastEventId: lastEventIdRef.current,
              signal: resumeController.signal,
              onEvent: resumeOnEvent,
            });
          } else {
            throw err;
          }
        } catch (resumeErr) {
          if (
            resumeErr instanceof Error &&
            (resumeErr.name === "AbortError" || cancelRequestedRef.current)
          ) {
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantId ? { ...m, status: "cancelled" } : m,
              ),
            );
          } else {
            const readable = formatApiError(resumeErr, formatApiError(err, "Stream failed"));
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantId
                  ? {
                      ...m,
                      content: m.content || readable,
                      status: "error",
                    }
                  : m,
              ),
            );
            setError(readable);
          }
        }
      } else {
        const readable = formatApiError(err, "Stream failed");
        setMessages((prev) =>
          prev.map((m) =>
            m.id === assistantId
              ? {
                  ...m,
                  content: m.content || readable,
                  status: "error",
                }
              : m,
          ),
        );
        setError(readable);
      }
    } finally {
      setStreaming(false);
      abortRef.current = null;
    }
  }

  const shellClass = embedded
    ? "min-h-[100dvh] text-ink"
    : "min-h-screen text-ink";

  if (!authReady) {
    return (
      <div
        style={cssVars}
        data-theme={dark ? "dark" : undefined}
        className={`app-canvas ${shellClass}`}
      >
        <div className="flex min-h-[40vh] items-center justify-center px-6">
          <p className="text-sm text-slate-muted">
            {!isLoaded || !isSignedIn
              ? "Redirecting to sign in…"
              : "Loading workspace…"}
          </p>
        </div>
      </div>
    );
  }

  return (
    <div
      style={cssVars}
      data-theme={dark ? "dark" : undefined}
      className={`app-canvas ${shellClass}`}
    >
      <div
        className={`relative ${embedded ? "min-h-[100dvh]" : "min-h-screen"}`}
        style={
          dark
            ? {
                background: `
            radial-gradient(1000px 500px at 15% 0%, color-mix(in oklab, var(--tenant-accent) 35%, transparent), transparent 55%),
            linear-gradient(160deg, var(--tenant-primary) 0%, #04110c 55%, #020807 100%)
          `,
              }
            : undefined
        }
      >
        <div className="pointer-events-none absolute inset-0 opacity-[0.14] grid-noise" />
        <div
          className={`relative mx-auto grid ${
            embedded
              ? "min-h-[100dvh] max-w-none"
              : "h-screen max-w-6xl lg:grid-cols-[220px_minmax(0,1fr)]"
          }`}
        >
          {!embedded ? (
            <aside className="flex flex-col border-b border-line p-5 lg:border-b-0 lg:border-r">
              <div>
                <p className="font-display text-2xl font-semibold tracking-tight text-ink">
                  {surface.tenant.name}
                </p>
                {surface.tenant.tagline ? (
                  <p className="mt-1.5 text-sm text-slate-muted">
                    {surface.tenant.tagline}
                  </p>
                ) : (
                  <p className="mt-1.5 text-xs uppercase tracking-[0.14em] text-slate-muted">
                    Workspace chat
                  </p>
                )}
              </div>
              <div className="mt-8 flex min-h-0 flex-1 flex-col">
                <SessionPicker
                  sessions={sessions}
                  activeId={activeSessionId}
                  onSelect={(id) => selectSession(id)}
                  onCreate={createSession}
                  onDelete={(id) => removeSession(id)}
                />
              </div>
              <Link
                href={`/t/${surface.tenant.slug}/chat`}
                className="mt-4 text-xs font-medium text-slate-muted transition hover:text-[var(--tenant-accent)]"
              >
                ← All workflows & teams
              </Link>
            </aside>
          ) : null}

          <section
            className={`flex min-h-0 flex-col ${embedded ? "min-h-[100dvh]" : "h-screen"}`}
          >
            <header className="shrink-0 border-b border-line px-5 py-3.5">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-[var(--tenant-accent)]">
                    {baseTargetType === "workflow" ? "Workflow" : "Team"}
                    {!embedded ? ` · ${target.slug}` : ""}
                  </p>
                  <h1
                    className={`mt-1 truncate font-display font-semibold tracking-tight text-ink ${
                      embedded ? "text-xl" : "text-2xl"
                    }`}
                  >
                    {activeTargetName}
                  </h1>
                  {!embedded && target.description ? (
                    <p className="mt-1 line-clamp-2 max-w-2xl text-sm text-slate-muted">
                      {target.description}
                    </p>
                  ) : null}
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <NotificationBell />
                  <ThemeToggle theme={theme} onChange={changeTheme} />
                  {!embedded ? (
                    <Link
                      href={`/t/${surface.tenant.slug}/chat`}
                      className="rounded-lg border border-line bg-raised/70 px-3 py-1.5 text-xs font-medium text-slate-muted transition hover:border-[var(--tenant-accent)]/60 hover:text-ink"
                    >
                      {tCommon("backToWorkspace")}
                    </Link>
                  ) : null}
                  {!embedded ? (
                    <ChatAccountBar
                      tenantSlug={surface.tenant.slug}
                      signInRedirect={`/t/${surface.tenant.slug}/chat`}
                      serverSession={serverSession}
                    />
                  ) : null}
                </div>
              </div>
              {baseTargetType === "workflow" && workflowTeams.length > 0 ? (
                <div className="mt-3 flex flex-wrap gap-2">
                  <button
                    type="button"
                    onClick={() => {
                      setRunMode("workflow");
                    }}
                    className={`rounded-lg border px-3 py-1.5 text-xs font-medium transition ${
                      runMode === "workflow"
                        ? "border-[var(--tenant-accent)] bg-[var(--tenant-accent)]/10 text-ink"
                        : "border-line text-slate-muted hover:border-line-strong hover:text-ink"
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
                          ? "border-[var(--tenant-accent)] bg-[var(--tenant-accent)]/10 text-ink"
                          : "border-line text-slate-muted hover:border-line-strong hover:text-ink"
                      }`}
                    >
                      {team.stepName || team.name}
                    </button>
                  ))}
                </div>
              ) : null}
            </header>
            <ApprovalBanner visible={paused} />
            <ChatMessageList
              messages={messages}
              dark={dark}
              markdown={!embedded}
              targetName={activeTargetName}
              starters={[
                "What can you help me with?",
                "Walk me through the next steps.",
                "Summarize what this workflow is for.",
              ]}
              onStarter={(text) => {
                void send(text);
              }}
            />
            {error ? (
              <p className="shrink-0 px-5 py-2 text-xs text-amber">{error}</p>
            ) : null}
            <MessageComposer
              dark={dark}
              disabled={streaming || paused || !activeSessionId}
              streaming={streaming}
              onSend={send}
              onCancel={() => {
                void cancelActiveRun();
              }}
              placeholder={`Message ${activeTargetName}…`}
            />
          </section>
        </div>
      </div>
    </div>
  );
}
