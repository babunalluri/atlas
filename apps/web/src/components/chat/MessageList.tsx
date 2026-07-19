"use client";

import type { ChatMessage } from "@/lib/api/types";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/Badge";

export function ChatMessageList({
  messages,
  dark = false,
}: {
  messages: ChatMessage[];
  dark?: boolean;
}) {
  return (
    <div className="flex-1 space-y-4 overflow-y-auto px-4 py-4">
      {messages.map((message) => {
        if (message.role === "system") {
          return (
            <p
              key={message.id}
              className={cn(
                "rounded-lg px-3 py-2 text-xs leading-relaxed",
                dark ? "bg-white/5 text-slate-muted" : "bg-fog text-slate-muted",
              )}
            >
              {message.content}
            </p>
          );
        }

        const isUser = message.role === "user";
        return (
          <div
            key={message.id}
            className={cn("flex", isUser ? "justify-end" : "justify-start")}
          >
            <div
              className={cn(
                "max-w-[92%] rounded-2xl px-3.5 py-2.5 text-sm leading-relaxed",
                isUser
                  ? dark
                    ? "bg-teal text-white"
                    : "bg-ink text-canvas"
                  : dark
                    ? "bg-white/8 text-ink backdrop-blur-sm"
                    : "bg-raised text-ink border border-line",
              )}
            >
              <p className="whitespace-pre-wrap">{message.content || (message.status === "streaming" ? "…" : "")}</p>
              {(message.toolSpans?.length || message.status) && !isUser ? (
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {message.toolSpans?.map((span) => (
                    <Badge key={span.id} tone="info">
                      {span.name}:{span.status}
                    </Badge>
                  ))}
                  {message.status && message.status !== "complete" ? (
                    <Badge
                      tone={
                        message.status === "error"
                          ? "danger"
                          : message.status === "paused"
                            ? "warning"
                            : "neutral"
                      }
                    >
                      {message.status}
                    </Badge>
                  ) : null}
                </div>
              ) : null}
            </div>
          </div>
        );
      })}
    </div>
  );
}
