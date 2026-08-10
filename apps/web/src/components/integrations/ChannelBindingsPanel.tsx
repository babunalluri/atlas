"use client";

import { useState } from "react";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Input, Label, Select } from "@/components/ui/Field";
import {
  createChannelBinding,
  deleteChannelBinding,
  type ChannelBinding,
  type ChannelProvider,
  type CredentialSummary,
} from "@/lib/api/admin";
import { useAgentOsToken } from "@/lib/auth/token";

type TargetOption = { id: string; name: string; slug: string };

export function ChannelBindingsPanel({
  initialBindings,
  credentials,
  teams,
  workflows,
  tenantSlug,
}: {
  initialBindings: ChannelBinding[];
  credentials: CredentialSummary[];
  teams: TargetOption[];
  workflows: TargetOption[];
  tenantSlug: string;
}) {
  const { getAccessToken } = useAgentOsToken();
  const [items, setItems] = useState(initialBindings);
  const [provider, setProvider] = useState<ChannelProvider>("slack");
  const [credentialId, setCredentialId] = useState("");
  const [targetType, setTargetType] = useState<"team" | "workflow">("team");
  const [targetConfigId, setTargetConfigId] = useState("");
  const [externalJson, setExternalJson] = useState("{}");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const targets = targetType === "team" ? teams : workflows;
  const webhookHint = `/public/webhooks/${provider}?tenant=${tenantSlug}`;

  async function onCreate() {
    setBusy(true);
    setError(null);
    try {
      let externalConfig: Record<string, unknown> = {};
      try {
        externalConfig = JSON.parse(externalJson || "{}") as Record<string, unknown>;
      } catch {
        throw new Error("external_config must be valid JSON");
      }
      if (!credentialId || !targetConfigId) {
        throw new Error("Credential and target are required");
      }
      const created = await createChannelBinding(await getAccessToken(), {
        provider,
        credentialId,
        targetType,
        targetConfigId,
        externalConfig,
      });
      setItems((current) => [created, ...current]);
      setExternalJson("{}");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Create failed");
    } finally {
      setBusy(false);
    }
  }

  async function onDelete(id: string) {
    setError(null);
    try {
      await deleteChannelBinding(await getAccessToken(), id);
      setItems((current) => current.filter((item) => item.id !== id));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Delete failed");
    }
  }

  return (
    <section className="rounded-xl border border-line bg-raised/40 p-4">
      <div className="mb-3 flex flex-wrap items-start justify-between gap-2">
        <div>
          <h2 className="text-sm font-semibold">Messaging channels</h2>
          <p className="mt-1 text-xs text-slate-muted">
            Bind Slack / Telegram / WhatsApp credentials to a published team or
            workflow. Webhooks:{" "}
            <span className="mono-cell">{webhookHint}</span>
          </p>
        </div>
        <Badge tone="info">{items.length}</Badge>
      </div>

      {error ? (
        <p className="mb-3 rounded-md border border-rose/30 bg-rose/10 px-3 py-1.5 text-sm text-rose">
          {error}
        </p>
      ) : null}

      <div className="grid gap-2 md:grid-cols-2 lg:grid-cols-3">
        <div>
          <Label htmlFor="channel-provider">Provider</Label>
          <Select
            id="channel-provider"
            value={provider}
            onChange={(e) => setProvider(e.target.value as ChannelProvider)}
          >
            <option value="slack">Slack</option>
            <option value="telegram">Telegram</option>
            <option value="whatsapp">WhatsApp</option>
          </Select>
        </div>
        <div>
          <Label htmlFor="channel-credential">Credential</Label>
          <Select
            id="channel-credential"
            value={credentialId}
            onChange={(e) => setCredentialId(e.target.value)}
          >
            <option value="">Select credential</option>
            {credentials.map((credential) => (
              <option key={credential.id} value={credential.id}>
                {credential.name} ({credential.provider})
              </option>
            ))}
          </Select>
        </div>
        <div>
          <Label htmlFor="channel-target-type">Target type</Label>
          <Select
            id="channel-target-type"
            value={targetType}
            onChange={(e) => {
              setTargetType(e.target.value as "team" | "workflow");
              setTargetConfigId("");
            }}
          >
            <option value="team">Team</option>
            <option value="workflow">Workflow</option>
          </Select>
        </div>
        <div className="md:col-span-2">
          <Label htmlFor="channel-target">Published target</Label>
          <Select
            id="channel-target"
            value={targetConfigId}
            onChange={(e) => setTargetConfigId(e.target.value)}
          >
            <option value="">Select {targetType}</option>
            {targets.map((target) => (
              <option key={target.id} value={target.id}>
                {target.name} (/{target.slug})
              </option>
            ))}
          </Select>
        </div>
        <div className="md:col-span-2 lg:col-span-3">
          <Label htmlFor="channel-external">external_config JSON</Label>
          <Input
            id="channel-external"
            value={externalJson}
            onChange={(e) => setExternalJson(e.target.value)}
            placeholder='{"channel_id":"C…"} or {"phone_number_id":"…","verify_token":"…"}'
          />
        </div>
      </div>
      <div className="mt-3">
        <Button variant="accent" disabled={busy} onClick={() => void onCreate()}>
          {busy ? "Saving…" : "Add binding"}
        </Button>
      </div>

      <ul className="mt-4 space-y-2">
        {items.map((item) => (
          <li
            key={item.id}
            className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-line px-3 py-2"
          >
            <div className="min-w-0">
              <p className="text-sm font-medium">
                {item.provider} → {item.targetType}{" "}
                <span className="mono-cell text-slate-muted">
                  {item.targetConfigId}
                </span>
              </p>
              <p className="mono-cell truncate text-xs text-slate-muted">
                {JSON.stringify(item.externalConfig)}
              </p>
            </div>
            <div className="flex items-center gap-2">
              <Badge tone={item.active ? "success" : "neutral"}>
                {item.active ? "active" : "inactive"}
              </Badge>
              <Button
                size="sm"
                variant="danger"
                onClick={() => void onDelete(item.id)}
              >
                Delete
              </Button>
            </div>
          </li>
        ))}
        {items.length === 0 ? (
          <li className="py-6 text-center text-sm text-slate-muted">
            No channel bindings yet.
          </li>
        ) : null}
      </ul>
    </section>
  );
}
