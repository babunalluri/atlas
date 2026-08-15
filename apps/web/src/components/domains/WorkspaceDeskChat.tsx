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
  formatApiError,
  streamConfiguredTeam,
} from "@/lib/agentos/client";
import { DeskChatPills } from "@/components/domains/DeskChat";
import type { DomainBrokerTool, DomainChatTarget } from "@/lib/api/admin";
import type { ChatMessage } from "@/lib/api/types";
import { useAgentOsToken } from "@/lib/auth/token";

function newId(prefix: string) {
  return `${prefix}_${Math.random().toString(36).slice(2, 10)}`;
}

function deskWelcome(
  target: DomainChatTarget | null,
  brokerLabel: string,
): string {
  if (!target) {
    return "No desk chats are assigned to you yet. Ask your administrator to assign a team.";
  }
  if (target.slug === "learning") {
    return "You're in Learning. Ask concepts, or generic ticker questions like “What’s TCS doing?” — I can use quotes if a toolkit is assigned, but I won’t predict prices. Strategy math goes to Research; paper fills to Paper trading; holdings to Live trading.";
  }
  if (target.slug === "paper-trading") {
    return `You're in Paper trading. Practice signals with virtual capital. Research is for analysis; live orders stay on Live trading (${brokerLabel}).`;
  }
  if (target.slug === "live-trading") {
    return `You're in Live trading. Holdings, margin, and live orders use ${brokerLabel}. Research is for analysis; paper practice stays in Paper trading.`;
  }
  if (target.slug === "research") {
    return "You're in Research. I analyze stocks and defined F&O structures with tools — I won’t invent quotes, chains, or P&L. Research is for analysis; live orders stay on Live trading.";
  }
  return `You're in ${target.name}. Ask anything this team is set up to handle.`;
}

function deskStarters(
  target: DomainChatTarget | null,
  brokerNames: string[],
): string[] {
  if (target?.slug === "learning") {
    return [
      "How do I learn trading safely?",
      "Can you predict TCS for the next few hours?",
    ];
  }
  if (target?.slug === "paper-trading") {
    return ["Show my latest signals", "Walk me through a paper trade"];
  }
  if (target?.slug === "live-trading") {
    return [
      brokerNames.length
        ? `Check ${brokerNames[0]} holdings and positions`
        : "Show my holdings and margin",
      "Why is live disarmed?",
    ];
  }
  if (target?.slug === "research") {
    return [
      "What’s the trend on RELIANCE from the latest quote?",
      "Payoff for a bull call spread — I’ll need strikes and LTPs",
    ];
  }
  return [];
}

export function WorkspaceDeskChat({
  targets,
  brokerTools = [],
  allowPreview = true,
}: {
  targets: DomainChatTarget[];
  brokerTools?: DomainBrokerTool[];
  allowPreview?: boolean;
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
      return [
        {
          id: newId("welcome"),
          role: "assistant",
          content: deskWelcome(target, brokerLabel),
          createdAt: new Date().toISOString(),
          status: "complete",
        },
      ];
    },
    [brokerLabel],
  );

  useEffect(() => {
    if (targets.some((row) => row.id === teamId)) return;
    setTeamId(targets[0]?.id ?? "");
  }, [targets, teamId]);

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
        preview: allowPreview && !team.published,
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
        const readable = formatApiError(err, "Stream failed");
        setError(readable);
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
        <DeskChatPills
          targets={targets}
          selectedId={team?.id}
          onSelect={setTeamId}
        />
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
        starters={deskStarters(team, brokerNames)}
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
