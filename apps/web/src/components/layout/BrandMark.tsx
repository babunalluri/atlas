import { Link } from "@/i18n/navigation";

import { ORG_ADMIN_HREF } from "@/lib/auth/desk-admin";

export function BrandMark({
  compact = false,
  href = ORG_ADMIN_HREF,
  subtitle = "Multi-tenant control",
}: {
  compact?: boolean;
  href?: string;
  subtitle?: string;
}) {
  return (
    <Link href={href} className="group inline-flex items-center gap-3">
      <span className="relative flex h-9 w-9 items-center justify-center overflow-hidden rounded-md bg-ink text-teal-bright">
        <span className="absolute inset-0 opacity-40 grid-noise" />
        <span className="font-display text-lg font-bold tracking-tight">A</span>
      </span>
      {!compact ? (
        <span className="leading-tight">
          <span className="block font-display text-lg font-semibold tracking-tight text-ink group-hover:text-ink-soft">
            Atlas Agents
          </span>
          <span className="block text-[11px] uppercase tracking-[0.14em] text-slate-muted">
            {subtitle}
          </span>
        </span>
      ) : null}
    </Link>
  );
}
