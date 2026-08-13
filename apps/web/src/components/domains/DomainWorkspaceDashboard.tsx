import { Link } from "@/i18n/navigation";

import { MetricsDashboard } from "@/components/metrics/MetricsDashboard";
import { Button } from "@/components/ui/Button";
import type { DomainDashboard } from "@/lib/api/admin";

export function DomainWorkspaceDashboard({
  data,
  refreshing,
  onRefresh,
  error,
}: {
  data: DomainDashboard;
  refreshing?: boolean;
  onRefresh?: () => void;
  error?: string | null;
}) {
  const isGeneric = data.domain === "generic";

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
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
        </div>
        {onRefresh ? (
          <div className="flex flex-col items-end gap-2">
            <Button
              variant="secondary"
              size="sm"
              onClick={onRefresh}
              disabled={refreshing}
            >
              {refreshing ? "Refreshing…" : "Refresh"}
            </Button>
            {data.fetched_at ? (
              <p className="text-[11px] text-slate-muted">
                Last fetched {new Date(data.fetched_at).toLocaleString()}
              </p>
            ) : null}
          </div>
        ) : null}
      </header>
      {error ? (
        <p className="rounded-lg border border-rose/30 bg-rose/10 px-3 py-2 text-xs text-rose">
          {error}
        </p>
      ) : null}

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
