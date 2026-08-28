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
import { DeskChatPills, DeskChatStarters } from "@/components/domains/DeskChat";
import { useDeskChatDraftOptional } from "@/components/domains/DeskChatDraftContext";
import { ChevronLeftIcon } from "@/components/ui/icons";
import type { DomainBrokerTool, DomainChatTarget } from "@/lib/api/admin";
import type { ChatMessage } from "@/lib/api/types";
import { useAgentOsToken } from "@/lib/auth/token";
import { randomUuid } from "@/lib/random-uuid";

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
  if (target.slug === "signals-ops") {
    return "Monitor entry metrics in the panel → publish when entry_ready. Customers never see this board.";
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
  if (target?.slug === "signals-ops") {
    return ["Show current signal state", "Is entry ready?"];
  }
  return [];
}

export function WorkspaceDeskChat({
  targets,
  brokerTools = [],
  allowPreview = true,
  onCollapse,
}: {
  targets: DomainChatTarget[];
  brokerTools?: DomainBrokerTool[];
  allowPreview?: boolean;
  onCollapse?: () => void;
}) {
  const { getAccessToken, isLoaded, isSignedIn } = useAgentOsToken();
  const deskDraft = useDeskChatDraftOptional();
  const [teamId, setTeamId] = useState(targets[0]?.id ?? "");
  const team = targets.find((row) => row.id === teamId) ?? targets[0] ?? null;
  const [sessionId, setSessionId] = useState(() => randomUuid());
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [composerDraft, setComposerDraft] = useState<string | null>(null);
  const clearComposerDraft = useCallback(() => setComposerDraft(null), []);
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
    setSessionId(randomUuid());
    setMessages(welcome(team));
    // Reset conversation when the selected desk team changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [team?.id]);

  useEffect(() => {
    return () => abortRef.current?.abort();
  }, []);

  useEffect(() => {
    if (!deskDraft?.pendingTeamId || !deskDraft.pendingDraft) return;
    if (!targets.some((row) => row.id === deskDraft.pendingTeamId)) return;
    setTeamId(deskDraft.pendingTeamId);
    setComposerDraft(deskDraft.pendingDraft);
    deskDraft.consumePending();
  }, [
    deskDraft,
    deskDraft?.pendingDraft,
    deskDraft?.pendingTeamId,
    targets,
  ]);

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

  const starters = deskStarters(team, brokerNames);
  const showStarters =
    starters.length > 0 &&
    messages.length <= 1 &&
    !messages.some((m) => m.role === "user");

  return (
    <div id="desk-chat" className="flex h-full min-h-0 flex-col">
      <header className="shrink-0 border-b border-line px-2 py-1.5">
        <div className="flex items-center gap-1">
          <div className="min-w-0 flex-1">
            <DeskChatPills
              targets={targets}
              selectedId={team?.id}
              onSelect={setTeamId}
              compact
            />
          </div>
          {team && !team.published ? (
            <span className="shrink-0 text-[10px] font-medium text-amber">
              Preview
            </span>
          ) : null}
          {onCollapse ? (
            <button
              type="button"
              onClick={onCollapse}
              title="Hide desk chat"
              aria-label="Hide desk chat"
              className="shrink-0 rounded p-1 text-slate-muted hover:bg-raised/70 hover:text-ink"
            >
              <ChevronLeftIcon className="h-3.5 w-3.5" />
            </button>
          ) : null}
        </div>
      </header>
      <ApprovalBanner visible={paused} />
      {error ? (
        <p className="shrink-0 border-b border-rose/30 bg-rose/10 px-3 py-1.5 text-xs text-rose">
          {error}
        </p>
      ) : null}
      <ChatMessageList
        messages={messages}
        markdown
        compact
        targetName={team?.name}
      />
      {showStarters ? (
        <DeskChatStarters
          prompts={starters}
          onSelect={(text) => void send(text)}
        />
      ) : null}
      <MessageComposer
        onSend={(text) => void send(text)}
        onCancel={() => void cancelActiveRun()}
        streaming={streaming}
        disabled={!team || streaming}
        compact
        placeholder={
          team ? `Message ${team.name}…` : "No desk team available"
        }
        externalDraft={composerDraft}
        onExternalDraftApplied={clearComposerDraft}
      />
    </div>
  );
}
