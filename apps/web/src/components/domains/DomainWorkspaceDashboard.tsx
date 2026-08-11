import { Link } from "@/i18n/navigation";

import { Badge } from "@/components/ui/Badge";
import { MetricsDashboard } from "@/components/metrics/MetricsDashboard";
import type { DomainDashboard } from "@/lib/api/admin";

export function DomainWorkspaceDashboard({ data }: { data: DomainDashboard }) {
  const isGeneric = data.domain === "generic";

  return (
    <div className="space-y-6">
      <header>
        <p className="text-xs font-semibold uppercase tracking-[0.16em] text-teal">
          {data.domain_label} workspace
        </p>
        <h1 className="mt-2 font-display text-4xl font-semibold tracking-tight">
          {isGeneric ? "Tenant metrics" : `${data.domain_label} dashboard`}
        </h1>
        <p className="mt-2 max-w-2xl text-sm text-slate-muted">
          {isGeneric
            ? `Operational metrics for the last ${data.range_days} days.`
            : `Domain-specific KPIs and operational metrics for the last ${data.range_days} days.`}
        </p>
      </header>

      {!isGeneric ? (
        <>
          <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {data.widgets.map((widget) => (
              <div key={widget.id} className="surface-panel rounded-xl p-4">
                <p className="th-label">{widget.label}</p>
                <p className="mt-2 text-xl font-semibold tnum">{widget.value}</p>
                <p className="mt-1 text-xs text-slate-muted">{widget.hint}</p>
              </div>
            ))}
          </section>

          {data.quick_links.length > 0 ? (
            <section className="flex flex-wrap gap-2">
              {data.quick_links.map((link) => (
                <Link
                  key={link.href + link.label}
                  href={link.href}
                  className="rounded-full border border-line px-3 py-1.5 text-xs font-medium text-ink-soft transition hover:border-teal/40 hover:text-teal"
                >
                  {link.label}
                </Link>
              ))}
            </section>
          ) : null}

          <section className="grid gap-3 sm:grid-cols-3">
            <div className="surface-panel rounded-xl p-4">
              <p className="th-label">Agents</p>
              <p className="mt-2 text-xl font-semibold tnum">
                {data.catalog.published_agents}/{data.catalog.agents}
              </p>
              <p className="mt-1 text-xs text-slate-muted">Published / total</p>
            </div>
            <div className="surface-panel rounded-xl p-4">
              <p className="th-label">Teams</p>
              <p className="mt-2 text-xl font-semibold tnum">
                {data.catalog.published_teams}/{data.catalog.teams}
              </p>
              <p className="mt-1 text-xs text-slate-muted">Published / total</p>
            </div>
            <div className="surface-panel rounded-xl p-4">
              <p className="th-label">Workflows</p>
              <p className="mt-2 text-xl font-semibold tnum">
                {data.catalog.published_workflows}/{data.catalog.workflows}
              </p>
              <p className="mt-1 text-xs text-slate-muted">Published / total</p>
            </div>
          </section>
        </>
      ) : null}

      <MetricsDashboard data={data.metrics} />
    </div>
  );
}
