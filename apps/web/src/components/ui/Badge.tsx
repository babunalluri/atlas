import { cn } from "@/lib/utils";

const tones = {
  neutral: "bg-fog/80 text-ink-soft",
  success: "bg-teal/15 text-teal",
  warning: "bg-amber/15 text-amber",
  danger: "bg-rose/15 text-rose",
  info: "bg-info/12 text-info",
} as const;

export function Badge({
  children,
  tone = "neutral",
  dot = false,
  live = false,
  className,
}: {
  children: React.ReactNode;
  tone?: keyof typeof tones;
  /** Render a small status dot before the label. */
  dot?: boolean;
  /** Render a pulsing live indicator dot (implies dot). */
  live?: boolean;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded px-2 py-0.5 text-[11px] font-semibold uppercase tracking-[0.06em]",
        tones[tone],
        className,
      )}
    >
      {live ? (
        <span className="live-dot" aria-hidden />
      ) : dot ? (
        <span className="size-1.5 rounded-full bg-current" aria-hidden />
      ) : null}
      {children}
    </span>
  );
}
