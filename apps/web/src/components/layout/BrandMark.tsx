import { Link } from "@/i18n/navigation";

import { ORG_ADMIN_HREF } from "@/lib/auth/desk-admin";
import { cn } from "@/lib/utils";

export function BrandMark({
  compact = false,
  size = "md",
  href = ORG_ADMIN_HREF,
  subtitle = "Multi-tenant control",
}: {
  compact?: boolean;
  /** ``lg`` for the marketing home header; ``md`` elsewhere. */
  size?: "md" | "lg";
  href?: string;
  subtitle?: string;
}) {
  const large = size === "lg";
  return (
    <Link
      href={href}
      className={cn(
        "group inline-flex items-center",
        large ? "gap-3.5" : "gap-3",
      )}
    >
      <span
        className={cn(
          "relative flex items-center justify-center overflow-hidden bg-ink text-teal-bright shadow-sm",
          large ? "h-16 w-16 rounded-2xl sm:h-[4.5rem] sm:w-[4.5rem]" : "h-9 w-9 rounded-md",
        )}
      >
        <span className="absolute inset-0 opacity-40 grid-noise" />
        <span
          className={cn(
            "font-display font-bold tracking-tight",
            large ? "text-4xl sm:text-5xl" : "text-lg",
          )}
        >
          A
        </span>
      </span>
      {!compact ? (
        <span className="leading-tight">
          <span
            className={cn(
              "block font-display font-semibold tracking-tight text-ink group-hover:text-ink-soft",
              large
                ? "text-4xl sm:text-5xl lg:text-[3.35rem] lg:leading-[1.08]"
                : "text-lg",
            )}
          >
            Atlas Agents
          </span>
          <span
            className={cn(
              "block uppercase text-slate-muted",
              large
                ? "mt-1 text-sm font-medium tracking-[0.18em]"
                : "text-[11px] tracking-[0.14em]",
            )}
          >
            {subtitle}
          </span>
        </span>
      ) : null}
    </Link>
  );
}
