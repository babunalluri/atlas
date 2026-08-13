"use client";

import { useState } from "react";

import { DomainWorkspaceDashboard } from "@/components/domains/DomainWorkspaceDashboard";
import { StockBrokerWorkspace } from "@/components/domains/StockBrokerWorkspace";
import { getDomainDashboard, type DomainDashboard } from "@/lib/api/admin";
import { useAgentOsToken } from "@/lib/auth/token";

export function DomainWorkspacePage({
  initialData,
}: {
  initialData: DomainDashboard;
}) {
  const { getAccessToken } = useAgentOsToken();
  const [data, setData] = useState(initialData);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function refresh() {
    setRefreshing(true);
    setError(null);
    try {
      const token = await getAccessToken();
      if (!token) throw new Error("Sign in to refresh the workspace.");
      setData(await getDomainDashboard(token, data.range_days, true));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Refresh failed");
    } finally {
      setRefreshing(false);
    }
  }

  if (data.domain === "stock_broker") {
    return (
      <div className="flex h-full min-h-0 flex-col">
        {error ? (
          <p className="shrink-0 border-b border-rose/30 bg-rose/10 px-5 py-2 text-xs text-rose">
            {error}
          </p>
        ) : null}
        <StockBrokerWorkspace
          data={data}
          refreshing={refreshing}
          onRefresh={() => void refresh()}
        />
      </div>
    );
  }

  return (
    <div className="h-full overflow-y-auto px-5 py-6">
      <DomainWorkspaceDashboard
        data={data}
        refreshing={refreshing}
        onRefresh={() => void refresh()}
        error={error}
      />
    </div>
  );
}
