"use client";

import type { ChatMessage } from "@/lib/api/types";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/Badge";

export function ChatMessageList({
  messages,
  dark = false,
  starters,
  onStarter,
  targetName,
}: {
  messages: ChatMessage[];
  dark?: boolean;
  starters?: string[];
  onStarter?: (text: string) => void;
  targetName?: string;
}) {
  const showStarters =
    Boolean(starters?.length && onStarter) &&
    messages.length <= 1 &&
    !messages.some((m) => m.role === "user");

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">
      <div className="mx-auto flex w-full max-w-3xl flex-1 flex-col gap-4 px-5 py-6">
        {messages.map((message) => {
          if (message.role === "system") {
            return (
              <p
                key={message.id}
                className={cn(
                  "rounded-lg px-3 py-2 text-xs leading-relaxed",
                  dark
                    ? "bg-white/5 text-white/55"
                    : "bg-fog text-slate-muted",
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
                  "max-w-[min(100%,36rem)] rounded-2xl px-4 py-3 text-sm leading-relaxed",
                  isUser
                    ? dark
                      ? "bg-[var(--tenant-accent,#18c4a8)] text-slate-950"
                      : "bg-ink text-canvas"
                    : dark
                      ? "border border-white/10 bg-white/[0.07] text-white"
                      : "border border-line bg-raised text-ink",
                )}
              >
                {!isUser ? (
                  <p
                    className={cn(
                      "mb-1.5 text-[10px] font-semibold uppercase tracking-[0.12em]",
                      dark ? "text-[var(--tenant-accent)]" : "text-teal",
                    )}
                  >
                    {targetName || "Assistant"}
                  </p>
                ) : null}
                <p className="whitespace-pre-wrap">
                  {message.content ||
                    (message.status === "streaming" ? "…" : "")}
                </p>
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

        {showStarters ? (
          <div className="mt-2 border-t border-white/10 pt-5">
            <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-white/45">
              Try asking
            </p>
            <div className="mt-3 flex flex-wrap gap-2">
              {starters!.map((prompt) => (
                <button
                  key={prompt}
                  type="button"
                  onClick={() => onStarter?.(prompt)}
                  className="rounded-xl border border-white/12 bg-white/[0.04] px-3.5 py-2.5 text-left text-sm text-white/75 transition hover:border-[var(--tenant-accent)]/50 hover:bg-white/[0.08] hover:text-white"
                >
                  {prompt}
                </button>
              ))}
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}
