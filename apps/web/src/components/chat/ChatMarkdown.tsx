"use client";

import { useMemo } from "react";
import type { Components } from "react-markdown";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { cn } from "@/lib/utils";

function isSafeHref(href: string | undefined): href is string {
  if (!href) return false;
  const lower = href.trim().toLowerCase();
  return (
    lower.startsWith("http://") ||
    lower.startsWith("https://") ||
    lower.startsWith("mailto:") ||
    lower.startsWith("/") ||
    lower.startsWith("#")
  );
}

function markdownComponents(dark: boolean): Components {
  const linkClass = dark
    ? "font-medium text-[var(--tenant-accent,#18c4a8)] underline-offset-2 hover:underline"
    : "font-medium text-teal underline-offset-2 hover:underline";
  const codeInline = dark
    ? "rounded bg-white/10 px-1.5 py-0.5 font-mono text-[0.85em] text-white/90"
    : "rounded bg-fog/80 px-1.5 py-0.5 font-mono text-[0.85em] text-ink-soft";
  const preClass = dark
    ? "my-3 overflow-x-auto rounded-xl border border-white/10 bg-black/35 p-3 font-mono text-[0.82em] leading-relaxed text-white/90"
    : "my-3 overflow-x-auto rounded-xl border border-line bg-fog/40 p-3 font-mono text-[0.82em] leading-relaxed text-ink-soft";
  const headingMuted = dark ? "text-white/45" : "text-slate-muted";
  const blockquoteClass = dark
    ? "my-3 border-l-2 border-[var(--tenant-accent,#18c4a8)]/50 pl-3 text-white/75"
    : "my-3 border-l-2 border-teal/40 pl-3 text-slate-muted";
  const tableWrap = "my-3 overflow-x-auto rounded-lg border";
  const tableClass = dark
    ? "min-w-full border-collapse text-left text-xs text-white/85"
    : "min-w-full border-collapse text-left text-xs text-ink-soft";
  const thClass = dark
    ? "border-b border-white/10 bg-white/5 px-3 py-2 font-semibold"
    : "border-b border-line bg-fog/50 px-3 py-2 font-semibold";
  const tdClass = dark
    ? "border-b border-white/8 px-3 py-2 align-top"
    : "border-b border-line/70 px-3 py-2 align-top";

  return {
    h1: ({ children }) => (
      <h1 className="mb-3 mt-1 font-display text-xl font-semibold tracking-tight first:mt-0">
        {children}
      </h1>
    ),
    h2: ({ children }) => (
      <h2 className="mb-2.5 mt-4 font-display text-lg font-semibold tracking-tight first:mt-0">
        {children}
      </h2>
    ),
    h3: ({ children }) => (
      <h3 className="mb-2 mt-3 text-base font-semibold first:mt-0">{children}</h3>
    ),
    p: ({ children }) => (
      <p className="mb-3 last:mb-0 [&:not(:first-child)]:mt-0">{children}</p>
    ),
    ul: ({ children }) => (
      <ul className="mb-3 list-disc space-y-1.5 pl-5 last:mb-0">{children}</ul>
    ),
    ol: ({ children }) => (
      <ol className="mb-3 list-decimal space-y-1.5 pl-5 last:mb-0">{children}</ol>
    ),
    li: ({ children }) => <li className="leading-relaxed">{children}</li>,
    strong: ({ children }) => (
      <strong className="font-semibold text-inherit">{children}</strong>
    ),
    em: ({ children }) => <em className="italic">{children}</em>,
    a: ({ href, children }) =>
      isSafeHref(href) ? (
        <a href={href} target="_blank" rel="noopener noreferrer" className={linkClass}>
          {children}
        </a>
      ) : (
        <span className={linkClass}>{children}</span>
      ),
    blockquote: ({ children }) => (
      <blockquote className={blockquoteClass}>{children}</blockquote>
    ),
    hr: () => (
      <hr
        className={cn("my-4 border-0 border-t", dark ? "border-white/10" : "border-line")}
      />
    ),
    code: ({ className, children, ...props }) => {
      const isBlock = Boolean(className);
      if (isBlock) {
        return (
          <code className={cn("block whitespace-pre", className)} {...props}>
            {children}
          </code>
        );
      }
      return (
        <code className={codeInline} {...props}>
          {children}
        </code>
      );
    },
    pre: ({ children }) => <pre className={preClass}>{children}</pre>,
    table: ({ children }) => (
      <div className={cn(tableWrap, dark ? "border-white/10" : "border-line")}>
        <table className={tableClass}>{children}</table>
      </div>
    ),
    thead: ({ children }) => <thead>{children}</thead>,
    tbody: ({ children }) => <tbody>{children}</tbody>,
    tr: ({ children }) => <tr>{children}</tr>,
    th: ({ children }) => <th className={thClass}>{children}</th>,
    td: ({ children }) => <td className={tdClass}>{children}</td>,
    del: ({ children }) => (
      <del className={cn("line-through", headingMuted)}>{children}</del>
    ),
  };
}

/** Renders assistant markdown for hosted workspace chat. */
export function ChatMarkdown({
  content,
  dark = false,
}: {
  content: string;
  dark?: boolean;
}) {
  const components = useMemo(() => markdownComponents(dark), [dark]);

  return (
    <div className="chat-markdown break-words leading-relaxed [&>*:first-child]:mt-0 [&>*:last-child]:mb-0">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {content}
      </ReactMarkdown>
    </div>
  );
}
