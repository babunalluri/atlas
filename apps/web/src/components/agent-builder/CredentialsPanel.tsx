"use client";

import { useEffect, useState } from "react";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Input, Label } from "@/components/ui/Field";
import { SearchableSelect } from "@/components/ui/SearchableSelect";
import { TrashIcon } from "@/components/ui/icons";
import {
  createCredential,
  deleteCredential,
  type CredentialSummary,
} from "@/lib/api/admin";
import {
  MODEL_PROVIDER_LABELS,
  type ModelProvider,
} from "@/lib/api/types";
import { useAgentOsToken } from "@/lib/auth/token";
import { formatRelative } from "@/lib/utils";

export type ProviderGroup = {
  label: string;
  providers: string[];
};

const DEFAULT_PROVIDER_GROUPS: ProviderGroup[] = [
  {
    label: "LLM",
    providers: ["openai", "anthropic", "groq", "moonshot", "nvidia", "gemini"],
  },
  { label: "Tools / integrations", providers: ["rest_api"] },
];

function providerLabel(provider: string): string {
  if (provider in MODEL_PROVIDER_LABELS) {
    return MODEL_PROVIDER_LABELS[provider as ModelProvider];
  }
  if (provider === "rest_api") return "REST API";
  return provider
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function groupsFromFlatOptions(options: string[]): ProviderGroup[] {
  const llmKeys = new Set(Object.keys(MODEL_PROVIDER_LABELS));
  const llm = options.filter((option) => llmKeys.has(option));
  const tools = options.filter((option) => !llmKeys.has(option));
  return [
    ...(llm.length ? [{ label: "LLM", providers: llm }] : []),
    ...(tools.length
      ? [{ label: "Tools / integrations", providers: tools }]
      : []),
  ];
}

function firstProvider(
  groups: ProviderGroup[],
): CredentialSummary["provider"] {
  const first = groups.flatMap((group) => group.providers)[0];
  return (first ?? "openai") as CredentialSummary["provider"];
}

export function CredentialsPanel({
  initial,
  providerGroups = DEFAULT_PROVIDER_GROUPS,
  providerOptions,
}: {
  initial: CredentialSummary[];
  providerGroups?: ProviderGroup[];
  /** Flat list still works; options are split into LLM vs tools groups. */
  providerOptions?: string[];
}) {
  const groups = providerOptions
    ? groupsFromFlatOptions(providerOptions)
    : providerGroups;
  const availableKey = groups.flatMap((group) => group.providers).join("|");

  const [credentials, setCredentials] = useState(initial);
  const [name, setName] = useState("");
  const [provider, setProvider] =
    useState<CredentialSummary["provider"]>(() => firstProvider(groups));
  const [value, setValue] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [deletingIds, setDeletingIds] = useState<Set<string>>(() => new Set());
  const { getAccessToken } = useAgentOsToken();

  useEffect(() => {
    const available = availableKey ? availableKey.split("|") : [];
    if (available.length > 0 && !available.includes(provider)) {
      setProvider(available[0] as CredentialSummary["provider"]);
    }
  }, [availableKey, provider]);

  async function save() {
    if (!name.trim() || !value) return;
    setSaving(true);
    setError(null);
    try {
      const created = await createCredential(await getAccessToken(), {
        name: name.trim(),
        provider,
        value,
      });
      setCredentials((current) => [created, ...current]);
      setName("");
      setValue("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Save failed");
    } finally {
      setSaving(false);
    }
  }

  async function onDelete(credential: CredentialSummary) {
    if (
      !window.confirm(
        `Delete “${credential.name}”? This cannot be undone.`,
      )
    ) {
      return;
    }
    setDeletingIds((current) => new Set(current).add(credential.id));
    setError(null);
    try {
      await deleteCredential(await getAccessToken(), credential.id);
      setCredentials((current) =>
        current.filter((item) => item.id !== credential.id),
      );
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Failed to delete credential",
      );
    } finally {
      setDeletingIds((current) => {
        const next = new Set(current);
        next.delete(credential.id);
        return next;
      });
    }
  }

  return (
    <div className="space-y-6">
      <header>
        <p className="text-xs font-semibold uppercase tracking-[0.14em] text-teal">
          Credentials
        </p>
        <h1 className="font-display text-4xl font-semibold tracking-tight">
          Tenant BYOK
        </h1>
        <p className="mt-2 max-w-2xl text-sm text-slate-muted">
          Secrets are encrypted server-side and are never returned after creation.
        </p>
      </header>

      <section className="surface-panel rounded-2xl p-5">
        <div className="grid gap-3 md:grid-cols-[1fr_0.8fr_1.4fr_auto] md:items-end">
          <div>
            <Label htmlFor="credential-name">Label</Label>
            <Input
              id="credential-name"
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="Production OpenAI"
            />
          </div>
          <div>
            <Label htmlFor="credential-provider">Provider</Label>
            <SearchableSelect
              id="credential-provider"
              value={provider}
              onChange={(value) =>
                setProvider(value as CredentialSummary["provider"])
              }
              options={groups.flatMap((group) =>
                group.providers.map((option) => ({
                  value: option,
                  label: providerLabel(option),
                })),
              )}
            />
          </div>
          <div>
            <Label htmlFor="credential-value">Secret</Label>
            <Input
              id="credential-value"
              type="password"
              value={value}
              onChange={(event) => setValue(event.target.value)}
              autoComplete="off"
            />
          </div>
          <Button
            variant="accent"
            disabled={!name.trim() || !value || saving}
            onClick={save}
          >
            {saving ? "Encrypting…" : "Save"}
          </Button>
        </div>
        {error ? <p className="mt-2 text-sm text-rose">{error}</p> : null}
      </section>

      <ul className="table-shell rounded-xl">
        {credentials.map((credential) => {
          const deleting = deletingIds.has(credential.id);
          return (
            <li
              key={credential.id}
              className="flex items-center justify-between gap-4 border-b border-line/60 px-4 py-2.5 last:border-0"
            >
              <div className="min-w-0">
                <p className="truncate text-sm font-medium">{credential.name}</p>
                <p className="mono-cell truncate text-slate-muted">
                  {credential.id}
                </p>
              </div>
              <div className="flex shrink-0 items-center gap-3">
                <div className="text-right">
                  <Badge dot tone="info">
                    {providerLabel(credential.provider)}
                  </Badge>
                  <p className="mono-cell mt-1 text-slate-muted">
                    {credential.keyVersion} ·{" "}
                    {formatRelative(credential.createdAt)}
                  </p>
                </div>
                <Button
                  size="icon"
                  variant="danger"
                  aria-label={`Delete ${credential.name}`}
                  title="Delete"
                  disabled={deleting}
                  onClick={() => void onDelete(credential)}
                >
                  {deleting ? "…" : <TrashIcon />}
                </Button>
              </div>
            </li>
          );
        })}
        {credentials.length === 0 ? (
          <li className="px-4 py-10 text-center text-sm text-slate-muted">
            No credentials yet — save one above.
          </li>
        ) : null}
      </ul>
    </div>
  );
}
