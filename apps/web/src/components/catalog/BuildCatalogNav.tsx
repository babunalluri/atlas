"use client";

import { Link, usePathname } from "@/i18n/navigation";

import { cn } from "@/lib/utils";

const LINKS = [
  { href: "/admin/agents", label: "Agents" },
  { href: "/admin/teams", label: "Teams" },
  { href: "/admin/workflows", label: "Workflows" },
] as const;

export function BuildCatalogNav() {
  const pathname = usePathname();
  return (
    <nav
      aria-label="Build catalog"
      className="flex flex-wrap items-center gap-1"
    >
      {LINKS.map((link) => {
        const active = pathname.startsWith(link.href);
        return (
          <Link
            key={link.href}
            href={link.href}
            prefetch={false}
            className={cn(
              "rounded-md border px-2.5 py-1.5 text-xs font-medium transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ink/20 focus-visible:ring-offset-1 focus-visible:ring-offset-canvas",
              active
                ? "border-line-strong bg-mist text-ink"
                : "border-transparent bg-raised text-slate-muted hover:bg-mist",
            )}
          >
            {link.label}
          </Link>
        );
      })}
    </nav>
  );
}
