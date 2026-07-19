import type { ButtonHTMLAttributes, PropsWithChildren } from "react";

import { cn } from "@/lib/utils";

type Variant = "primary" | "secondary" | "ghost" | "danger" | "accent";
type Size = "md" | "sm";

const variants: Record<Variant, string> = {
  primary:
    "bg-ink text-canvas hover:bg-ink-soft shadow-[0_1px_0_rgba(255,255,255,0.12)_inset]",
  secondary:
    "bg-raised text-ink border border-line hover:border-line-strong hover:bg-mist",
  ghost: "bg-transparent text-ink hover:bg-fog/70",
  danger: "bg-rose text-white hover:brightness-110",
  accent: "bg-teal text-white hover:bg-teal-bright",
};

const sizes: Record<Size, string> = {
  md: "px-3.5 py-2 text-sm",
  sm: "px-2.5 py-1 text-xs",
};

export function Button({
  children,
  className,
  variant = "primary",
  size = "md",
  ...props
}: PropsWithChildren<
  ButtonHTMLAttributes<HTMLButtonElement> & {
    variant?: Variant;
    size?: Size;
    className?: string;
  }
>) {
  return (
    <button
      className={cn(
        "inline-flex items-center justify-center gap-2 rounded-md font-medium tracking-tight transition disabled:cursor-not-allowed disabled:opacity-50",
        sizes[size],
        variants[variant],
        className,
      )}
      {...props}
    >
      {children}
    </button>
  );
}
