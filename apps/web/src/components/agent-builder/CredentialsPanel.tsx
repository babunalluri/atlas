"use client";

import { useState } from "react";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Input, Label, Select } from "@/components/ui/Field";
import {
  createCredential,
  type CredentialSummary,
} from "@/lib/api/admin";
import { useAgentOsToken } from "@/lib/auth/token";
import { formatRelative } from "@/lib/utils";

export function CredentialsPanel({
  initial,
  providerOptions = ["openai", "anthropic", "groq", "rest_api"],
}: {
  initial: CredentialSummary[];
  providerOptions?: string[];
}) {
  const [credentials, setCredentials] = useState(initial);
  const [name, setName] = useState("");
  const [provider, setProvider] =
    useState<CredentialSummary["provider"]>("openai");
  const [value, setValue] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const { getAccessToken } = useAgentOsToken();

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
            <Select
              id="credential-provider"
              value={provider}
              onChange={(event) =>
                setProvider(event.target.value as CredentialSummary["provider"])
              }
            >
              {providerOptions.map((option) => (
                <option key={option} value={option}>
                  {option.replaceAll("_", " ")}
                </option>
              ))}
            </Select>
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
        {credentials.map((credential) => (
          <li
            key={credential.id}
            className="flex items-center justify-between gap-4 border-b border-line/60 px-4 py-2.5 last:border-0"
          >
            <div className="min-w-0">
              <p className="truncate text-sm font-medium">{credential.name}</p>
              <p className="mono-cell truncate text-slate-muted">{credential.id}</p>
            </div>
            <div className="text-right">
              <Badge dot tone="info">{credential.provider}</Badge>
              <p className="mono-cell mt-1 text-slate-muted">
                {credential.keyVersion} · {formatRelative(credential.createdAt)}
              </p>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
