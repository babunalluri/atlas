"use client";

import { useMemo } from "react";
import Link from "next/link";

import { Label, Select } from "@/components/ui/Field";
import type { CredentialSummary } from "@/lib/api/admin";
import {
  ALLOWED_MODELS,
  MODEL_PROVIDER_LABELS,
  modelProvidersWithCredentials,
  providerForModel,
  type ModelId,
  type ModelProvider,
} from "@/lib/api/types";

export function ModelSelect({
  id,
  label = "Model",
  value,
  onChange,
  credentials,
}: {
  id: string;
  label?: string;
  value: ModelId;
  onChange: (model: ModelId) => void;
  credentials: CredentialSummary[];
}) {
  const availableProviders = useMemo(
    () => modelProvidersWithCredentials(credentials),
    [credentials],
  );

  const availableModels = useMemo(
    () =>
      ALLOWED_MODELS.filter((model) =>
        availableProviders.has(model.provider),
      ),
    [availableProviders],
  );

  const selected = ALLOWED_MODELS.find((model) => model.id === value);
  const selectedProvider = providerForModel(value);
  const selectedMissingCredential =
    Boolean(selected) && !availableProviders.has(selectedProvider);

  const allProviderLabels = (
    Object.keys(MODEL_PROVIDER_LABELS) as ModelProvider[]
  ).map((provider) => MODEL_PROVIDER_LABELS[provider]);

  return (
    <div>
      <Label htmlFor={id}>{label}</Label>
      <Select
        id={id}
        value={value}
        onChange={(event) => onChange(event.target.value as ModelId)}
      >
        {selectedMissingCredential && selected ? (
          <option value={selected.id}>
            {selected.label} (missing{" "}
            {MODEL_PROVIDER_LABELS[selectedProvider]} credential)
          </option>
        ) : null}
        {availableModels.map((model) => (
          <option key={model.id} value={model.id}>
            {model.label}
          </option>
        ))}
      </Select>
      {selectedMissingCredential ? (
        <p className="mt-1 text-xs text-amber">
          No {MODEL_PROVIDER_LABELS[selectedProvider]} credential for this
          model.{" "}
          <Link
            href="/admin/credentials"
            className="font-semibold text-teal hover:underline"
          >
            Add a credential
          </Link>
        </p>
      ) : null}
      {availableModels.length === 0 ? (
        <p className="mt-1 text-xs text-slate-muted">
          Add a credential for {allProviderLabels.join("/")} in{" "}
          <Link
            href="/admin/credentials"
            className="font-semibold text-teal hover:underline"
          >
            Credentials
          </Link>{" "}
          {selectedMissingCredential
            ? "to unlock other models."
            : "to choose a model."}
        </p>
      ) : null}
    </div>
  );
}
