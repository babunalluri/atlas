"use client";

import { useState } from "react";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import {
  setMcpServerEnabled,
  type McpServerSettings,
} from "@/lib/api/admin";
import { useAgentOsToken } from "@/lib/auth/token";

export function McpServerPanel({
  initial,
}: {
  initial: McpServerSettings;
}) {
  const [settings, setSettings] = useState(initial);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { getAccessToken } = useAgentOsToken();
  const publicBase =
    process.env.NEXT_PUBLIC_AGENTOS_URL?.replace(/\/$/, "") ??
    "http://localhost:7777";
  const endpoint = `${publicBase}${settings.endpoint}`;

  async function toggle() {
    setBusy(true);
    setError(null);
    try {
      setSettings(
        await setMcpServerEnabled(
          await getAccessToken(),
          !settings.enabled,
        ),
      );
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Update failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-6">
      <header className="flex flex-col justify-between gap-4 sm:flex-row sm:items-end">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.14em] text-teal">
            Settings
          </p>
          <h1 className="font-display text-4xl font-semibold tracking-tight">
            MCP server
          </h1>
          <p className="mt-2 max-w-2xl text-sm text-slate-muted">
            Let approved machine clients discover and run Atlas resources through
            a tenant-scoped Model Context Protocol endpoint.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <Badge tone={settings.enabled ? "success" : "neutral"}>
            {settings.status}
          </Badge>
          <Button
            variant={settings.enabled ? "ghost" : "accent"}
            disabled={busy}
            onClick={toggle}
          >
            {busy
              ? "Updating…"
              : settings.enabled
                ? "Disable server"
                : "Enable server"}
          </Button>
        </div>
      </header>

      {error ? <p className="text-sm text-rose">{error}</p> : null}

      <section className="surface-panel rounded-2xl p-5">
        <h2 className="text-lg font-semibold">Connect</h2>
        <p className="mt-1 text-sm text-slate-muted">
          Streamable HTTP endpoint (MCP {settings.protocol_version})
        </p>
        <code className="mt-4 block overflow-x-auto rounded-lg bg-[#071018] px-4 py-3 font-mono text-xs text-[#d7e0e8]">
          {endpoint}
        </code>
        <p className="mt-4 text-sm text-slate-muted">
          Send a service-account token as{" "}
          <code>Authorization: Bearer &lt;token&gt;</code>. Create that token
          under Access. Never put a token in the endpoint URL or commit it to
          client configuration.
        </p>
      </section>

      <div className="grid gap-5 lg:grid-cols-2">
        <section className="surface-panel rounded-2xl p-5">
          <h2 className="text-lg font-semibold">Least-privilege scopes</h2>
          <ul className="mt-3 space-y-2 text-sm text-slate-muted">
            <li><code>mcp:access</code> — connect and negotiate MCP.</li>
            <li><code>mcp:read</code> — discover tenant agents, teams, and workflows.</li>
            <li><code>mcp:run</code> — run published resources.</li>
            <li><code>mcp:sessions:read</code> — read accessible session metadata.</li>
          </ul>
          <p className="mt-4 text-xs text-slate-muted">
            Tenant administrators can use the server with their signed-in token.
            Other users need equivalent JWT scopes. Service accounts must carry
            each required scope explicitly.
          </p>
        </section>

        <section className="surface-panel rounded-2xl p-5">
          <h2 className="text-lg font-semibold">Security boundary</h2>
          <ul className="mt-3 list-disc space-y-2 pl-5 text-sm text-slate-muted">
            <li>Tenant identity comes only from verified token claims.</li>
            <li>Draft resources are never runnable through MCP.</li>
            <li>Runs can invoke the tools configured on the selected resource.</li>
            <li>Normal per-tenant API rate limits and concurrency limits apply.</li>
            <li>Disable this endpoint and revoke tokens when an integration is retired.</li>
          </ul>
        </section>
      </div>

      <section className="rounded-2xl border border-amber-300/40 bg-amber-50/60 p-5 text-sm text-amber-950">
        <h2 className="font-semibold">Outbound server, not an agent tool</h2>
        <p className="mt-1">
          This page controls Atlas as an MCP server for external clients. MCP
          connections configured under Tools are inbound client integrations used
          by agents and are unaffected.
        </p>
        {settings.limitations.map((limitation) => (
          <p key={limitation} className="mt-2">{limitation}</p>
        ))}
      </section>
    </div>
  );
}
