import type { ButtonHTMLAttributes, PropsWithChildren, ReactNode } from "react";

import { cn } from "@/lib/utils";

type Variant = "primary" | "secondary" | "ghost" | "danger" | "accent";
type Size = "md" | "sm" | "icon";

const base =
  "inline-flex items-center justify-center gap-1.5 rounded-md font-medium tracking-tight transition-[color,background-color,border-color,box-shadow,opacity,transform] duration-150 disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-1 focus-visible:ring-offset-canvas";

const variants: Record<Variant, string> = {
  primary:
    "bg-ink text-canvas hover:bg-ink-soft shadow-[0_1px_0_rgba(255,255,255,0.12)_inset] focus-visible:ring-ink/25",
  secondary:
    "bg-raised text-ink border border-line hover:border-line-strong hover:bg-mist focus-visible:ring-line-strong/50",
  ghost: "bg-transparent text-ink hover:bg-fog/70 focus-visible:ring-fog",
  // Soft outline — readable as destructive without solid coral bricks.
  danger:
    "border border-rose/35 bg-transparent text-rose hover:border-rose/55 hover:bg-rose/10 focus-visible:ring-rose/35",
  // Teal CTA: flat solid fill — no bevel/gradient chrome.
  accent:
    "bg-teal text-white hover:bg-teal-bright focus-visible:ring-teal-bright/45",
};

/** Icon-only danger: muted until hover so list rows don't shout. */
const dangerIcon =
  "border border-transparent bg-transparent text-slate-muted hover:border-rose/40 hover:bg-rose/10 hover:text-rose focus-visible:ring-rose/35 focus-visible:text-rose";

const sizes: Record<Size, string> = {
  md: "px-3.5 py-2 text-sm",
  sm: "px-2.5 py-1.5 text-xs",
  icon: "h-7 w-7 shrink-0 p-0 text-xs",
};

export function buttonClassName({
  variant = "primary",
  size = "md",
  className,
}: {
  variant?: Variant;
  size?: Size;
  className?: string;
}) {
  const variantClass =
    variant === "danger" && size === "icon" ? dangerIcon : variants[variant];
  return cn(base, sizes[size], variantClass, className);
}

export function Button({
  children,
  className,
  variant = "primary",
  size = "md",
  icon,
  ...props
}: PropsWithChildren<
  ButtonHTMLAttributes<HTMLButtonElement> & {
    variant?: Variant;
    size?: Size;
    className?: string;
    /** Optional leading icon (before label). */
    icon?: ReactNode;
  }
>) {
  return (
    <button
      className={buttonClassName({ variant, size, className })}
      {...props}
    >
      {icon ? <span className="inline-flex shrink-0">{icon}</span> : null}
      {children}
    </button>
  );
}
