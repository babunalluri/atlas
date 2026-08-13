"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { ApprovalBanner } from "@/components/chat/ApprovalBanner";
import { MessageComposer } from "@/components/chat/MessageComposer";
import { ChatMessageList } from "@/components/chat/MessageList";
import {
  extractTextContent,
  isPausedRunEvent,
  isTerminalRunEvent,
  type RunEventBase,
} from "@/lib/agentos/sse";
import {
  cancelConfiguredRun,
  streamConfiguredTeam,
} from "@/lib/agentos/client";
import type { DomainBrokerTool, DomainChatTarget } from "@/lib/api/admin";
import type { ChatMessage } from "@/lib/api/types";
import { useAgentOsToken } from "@/lib/auth/token";
import { cn } from "@/lib/utils";

function newId(prefix: string) {
  return `${prefix}_${Math.random().toString(36).slice(2, 10)}`;
}

export function WorkspaceDeskChat({
  targets,
  brokerTools = [],
}: {
  targets: DomainChatTarget[];
  brokerTools?: DomainBrokerTool[];
}) {
  const { getAccessToken, isLoaded, isSignedIn } = useAgentOsToken();
  const [teamId, setTeamId] = useState(targets[0]?.id ?? "");
  const team = targets.find((row) => row.id === teamId) ?? targets[0] ?? null;
  const [sessionId, setSessionId] = useState(() => crypto.randomUUID());
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [paused, setPaused] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const runIdRef = useRef<string | null>(null);
  const cancelRequestedRef = useRef(false);

  const teamBrokers = brokerTools.filter((tool) => {
    if (!tool.active) return false;
    if (!team?.slug || !tool.via_team) return true;
    return tool.via_team === team.slug;
  });
  const brokerNames = teamBrokers.map((tool) => tool.name);
  const brokerLabel = brokerNames.length
    ? brokerNames.join(", ")
    : "whatever toolkit is assigned on this team";

  const welcome = useCallback(
    (target: DomainChatTarget | null): ChatMessage[] => {
      let content =
        "Publish Learning, Paper trading, and Live trading to start chatting.";
      if (target?.slug === "learning") {
        content =
          "You're in Learning. Ask concepts, or generic ticker questions like “What’s TCS doing?” — I can use quotes if a toolkit is assigned, but I won’t predict prices. Paper fills go to Paper trading; holdings to Live trading.";
      } else if (target?.slug === "paper-trading") {
        content = `You're in Paper trading. Practice signals with virtual capital. Live demat uses ${brokerLabel} in Live trading.`;
      } else if (target?.slug === "live-trading") {
        content = `You're in Live trading. Holdings, margin, and live orders use ${brokerLabel}. Paper practice stays in Paper trading.`;
      }
      return [
        {
          id: newId("welcome"),
          role: "assistant",
          content,
          createdAt: new Date().toISOString(),
          status: "complete",
        },
      ];
    },
    [brokerLabel],
  );

  useEffect(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setStreaming(false);
    setPaused(false);
    setError(null);
    setSessionId(crypto.randomUUID());
    setMessages(welcome(team));
    // Reset conversation when the selected desk team changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [team?.id]);

  useEffect(() => {
    return () => abortRef.current?.abort();
  }, []);

  async function cancelActiveRun() {
    cancelRequestedRef.current = true;
    const runId = runIdRef.current;
    if (runId && team && sessionId) {
      try {
        const accessToken = await getAccessToken();
        if (accessToken) {
          await cancelConfiguredRun({
            accessToken,
            kind: "team",
            runId,
            sessionId,
            configId: team.id,
          });
        }
      } catch {
        // Best-effort stop.
      }
    }
    abortRef.current?.abort();
    abortRef.current = null;
    setStreaming(false);
  }

  async function send(text: string) {
    if (!team || streaming) return;
    const userMessage: ChatMessage = {
      id: newId("user"),
      role: "user",
      content: text,
      createdAt: new Date().toISOString(),
      status: "complete",
    };
    const assistantId = newId("assistant");
    setMessages((prev) => [
      ...prev,
      userMessage,
      {
        id: assistantId,
        role: "assistant",
        content: "",
        createdAt: new Date().toISOString(),
        status: "streaming",
      },
    ]);
    setError(null);
    setPaused(false);
    setStreaming(true);
    cancelRequestedRef.current = false;
    const controller = new AbortController();
    abortRef.current = controller;
    runIdRef.current = null;

    try {
      const accessToken = await getAccessToken();
      if (!accessToken) {
        throw new Error("Sign in to chat with the desk.");
      }
      await streamConfiguredTeam({
        accessToken,
        teamConfigId: team.id,
        message: text,
        sessionId,
        preview: !team.published,
        signal: controller.signal,
        onEvent: (event: RunEventBase) => {
          if (typeof event.run_id === "string") {
            runIdRef.current = event.run_id;
          }
          if (event.event === "RunContent" || event.event === "TeamRunContent") {
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
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantId ? { ...m, status: "paused" } : m,
              ),
            );
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
        },
      });
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
      } else {
        setError(err instanceof Error ? err.message : "Stream failed");
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
      }
    } finally {
      setStreaming(false);
      abortRef.current = null;
    }
  }

  if (!isLoaded || !isSignedIn) {
    return (
      <div className="flex h-full items-center justify-center px-4 text-sm text-slate-muted">
        Sign in to chat with the desk.
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="shrink-0 border-b border-line px-4 py-3">
        <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-teal">
          Desk chat
        </p>
        <div className="mt-2 flex flex-wrap gap-1.5">
          {targets.map((target) => (
            <button
              key={target.id}
              type="button"
              onClick={() => setTeamId(target.id)}
              className={cn(
                "rounded-full border px-2.5 py-1 text-xs font-medium transition",
                target.id === team?.id
                  ? "border-teal/40 bg-teal/10 text-teal"
                  : "border-line text-slate-muted hover:border-teal/30 hover:text-ink",
              )}
            >
              {target.name}
            </button>
          ))}
        </div>
        {team && !team.published ? (
          <p className="mt-2 text-[11px] text-amber">Preview — publish this team for live runs.</p>
        ) : null}
      </header>
      <ApprovalBanner visible={paused} />
      {error ? (
        <p className="shrink-0 border-b border-rose/30 bg-rose/10 px-4 py-2 text-xs text-rose">
          {error}
        </p>
      ) : null}
      <ChatMessageList
        messages={messages}
        markdown
        targetName={team?.name}
        starters={
          team?.slug === "learning"
            ? [
                "How do I learn trading safely?",
                "Can you predict TCS for the next few hours?",
              ]
            : team?.slug === "paper-trading"
              ? [
                  "Show my latest signals",
                  "Walk me through a paper trade",
                ]
            : team?.slug === "live-trading"
              ? [
                  brokerNames.length
                    ? `Check ${brokerNames[0]} holdings and positions`
                    : "Show my holdings and margin",
                  "Why is live disarmed?",
                ]
              : []
        }
        onStarter={(text) => void send(text)}
      />
      <MessageComposer
        onSend={(text) => void send(text)}
        onCancel={() => void cancelActiveRun()}
        streaming={streaming}
        disabled={!team || streaming}
        placeholder={
          team ? `Message ${team.name}…` : "No desk team available"
        }
      />
    </div>
  );
}
