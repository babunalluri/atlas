"use client";

import { useRef, useState } from "react";

import { ChatMessageList } from "@/components/chat/MessageList";
import { MessageComposer } from "@/components/chat/MessageComposer";
import { Badge } from "@/components/ui/Badge";
import {
  extractTextContent,
  isPausedRunEvent,
  isTerminalRunEvent,
  type RunEventBase,
} from "@/lib/agentos/sse";
import {
  streamConfiguredAgent,
  streamConfiguredTeam,
  streamConfiguredWorkflow,
} from "@/lib/agentos/client";
import type { ChatMessage } from "@/lib/api/types";
import { useAgentOsToken } from "@/lib/auth/token";

function newId(prefix: string) {
  return `${prefix}_${Math.random().toString(36).slice(2, 10)}`;
}

export function PreviewChat({
  agentId,
  agentName,
  targetType = "agent",
}: {
  agentId: string;
  agentName: string;
  targetType?: "agent" | "team" | "workflow";
}) {
  const { getAccessToken } = useAgentOsToken();
  const [messages, setMessages] = useState<ChatMessage[]>([
    {
      id: "sys_1",
      role: "system",
      content: `Preview uses the current draft of ${agentName}. Customer chat pins published versions.`,
      createdAt: new Date().toISOString(),
    },
  ]);
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [paused, setPaused] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const sessionRef = useRef<string>(newId("preview_sess"));

  async function send(text: string) {
    setError(null);
    setPaused(false);
    const userMsg: ChatMessage = {
      id: newId("user"),
      role: "user",
      content: text,
      createdAt: new Date().toISOString(),
      status: "complete",
    };
    const assistantId = newId("asst");
    setMessages((prev) => [
      ...prev,
      userMsg,
      {
        id: assistantId,
        role: "assistant",
        content: "",
        createdAt: new Date().toISOString(),
        status: "streaming",
        toolSpans: [],
      },
    ]);
    setStreaming(true);

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const token = await getAccessToken();
      const runOptions = {
        accessToken: token,
        message: text,
        sessionId: sessionRef.current,
        preview: true,
        signal: controller.signal,
        onEvent: (event: RunEventBase) => {
          if (event.session_id) {
            sessionRef.current = event.session_id;
          }
          if (event.event === "RunContent" || event.event === "RunIntermediateContent") {
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
          if (event.event === "ToolCallStarted") {
            const name =
              typeof event.tool === "object" &&
              event.tool &&
              "tool_name" in (event.tool as object)
                ? String((event.tool as { tool_name?: string }).tool_name)
                : "tool";
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantId
                  ? {
                      ...m,
                      toolSpans: [
                        ...(m.toolSpans ?? []),
                        {
                          id: newId("tool"),
                          name,
                          status: "running",
                        },
                      ],
                    }
                  : m,
              ),
            );
          }
          if (isPausedRunEvent(event)) {
            setPaused(true);
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantId ? { ...m, status: "paused" } : m,
              ),
            );
          }
          if (event.event === "RunError") {
            setError(String(event.error ?? "Run failed"));
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantId ? { ...m, status: "error" } : m,
              ),
            );
          }
          if (event.event === "RunCancelled") {
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantId ? { ...m, status: "cancelled" } : m,
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
        },
      };
      if (targetType === "team") {
        await streamConfiguredTeam({ ...runOptions, teamConfigId: agentId });
      } else if (targetType === "workflow") {
        await streamConfiguredWorkflow({
          ...runOptions,
          workflowConfigId: agentId,
        });
      } else {
        await streamConfiguredAgent({ ...runOptions, agentConfigId: agentId });
      }
    } catch (err) {
      if (controller.signal.aborted) {
        setMessages((prev) =>
          prev.map((m) =>
            m.id === assistantId ? { ...m, status: "cancelled" } : m,
          ),
        );
      } else {
        // Local mock stream when AgentOS is offline — keeps the editor usable.
        const fallback =
          "*(AgentOS unreachable — showing local stub.)* I would answer using the draft instructions, bound tools, and knowledge sources once the backend is available.";
        setMessages((prev) =>
          prev.map((m) =>
            m.id === assistantId
              ? { ...m, content: fallback, status: "complete" }
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

  function cancel() {
    abortRef.current?.abort();
  }

  return (
    <aside
      data-theme="dark"
      className="app-canvas flex h-[min(78vh,920px)] flex-col overflow-hidden rounded-2xl border border-line text-ink shadow-[0_24px_60px_rgba(4,10,14,0.35)]"
    >
      <div className="glass-bar flex items-center justify-between border-b border-line px-4 py-3">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-teal-bright">
            Preview
          </p>
          <p className="text-sm text-ink-soft">Draft run · admin only</p>
        </div>
        <div className="flex items-center gap-2">
          {paused ? <Badge dot live tone="warning">awaiting approval</Badge> : null}
          {streaming ? <Badge dot live tone="info">streaming</Badge> : null}
        </div>
      </div>
      <ChatMessageList messages={messages} dark />
      {error ? (
        <p className="border-t border-line px-4 py-2 text-xs text-amber">{error}</p>
      ) : null}
      <MessageComposer
        dark
        disabled={streaming}
        streaming={streaming}
        onSend={send}
        onCancel={cancel}
        placeholder="Try the draft…"
      />
    </aside>
  );
}
