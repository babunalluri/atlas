"use client";

import { useState } from "react";

import { DomainWorkspaceDashboard } from "@/components/domains/DomainWorkspaceDashboard";
import { getDomainDashboard, type DomainDashboard } from "@/lib/api/admin";
import { useAgentOsToken } from "@/lib/auth/token";

export function DomainMetricsPage({
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
      if (!token) throw new Error("Sign in to refresh metrics.");
      setData(await getDomainDashboard(token, data.range_days, false));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Refresh failed");
    } finally {
      setRefreshing(false);
    }
  }

  return (
    <DomainWorkspaceDashboard
      data={data}
      refreshing={refreshing}
      onRefresh={() => void refresh()}
      error={error}
    />
  );
}
