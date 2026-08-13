"use client";

import { useId, useState } from "react";

import { Button } from "@/components/ui/Button";
import { FieldHint, Input, Label } from "@/components/ui/Field";

const MIN_PASSWORD_LENGTH = 8;

export function passwordError(password: string, confirm: string): string | null {
  if (password.length < MIN_PASSWORD_LENGTH) {
    return `Password must be at least ${MIN_PASSWORD_LENGTH} characters`;
  }
  if (password !== confirm) {
    return "Passwords do not match";
  }
  return null;
}

export function SetPasswordForm({
  submitLabel,
  pendingLabel,
  onSubmit,
  includeCurrent = false,
}: {
  submitLabel: string;
  pendingLabel?: string;
  includeCurrent?: boolean;
  onSubmit: (values: {
    currentPassword?: string;
    password: string;
  }) => Promise<void>;
}) {
  const currentId = useId();
  const passwordId = useId();
  const confirmId = useId();
  const [currentPassword, setCurrentPassword] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  async function save() {
    const mismatch = passwordError(password, confirm);
    if (mismatch) {
      setError(mismatch);
      return;
    }
    if (includeCurrent && !currentPassword) {
      setError("Current password is required");
      return;
    }
    setBusy(true);
    setError(null);
    setDone(false);
    try {
      await onSubmit(
        includeCurrent
          ? { currentPassword, password }
          : { password },
      );
      setPassword("");
      setConfirm("");
      setCurrentPassword("");
      setDone(true);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not set password");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-3">
      {includeCurrent ? (
        <div>
          <Label htmlFor={currentId}>Current password</Label>
          <Input
            id={currentId}
            type="password"
            autoComplete="current-password"
            value={currentPassword}
            onChange={(event) => setCurrentPassword(event.target.value)}
          />
        </div>
      ) : null}
      <div>
        <Label htmlFor={passwordId}>New password</Label>
        <Input
          id={passwordId}
          type="password"
          autoComplete="new-password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
        />
        <FieldHint>At least {MIN_PASSWORD_LENGTH} characters</FieldHint>
      </div>
      <div>
        <Label htmlFor={confirmId}>Confirm password</Label>
        <Input
          id={confirmId}
          type="password"
          autoComplete="new-password"
          value={confirm}
          onChange={(event) => setConfirm(event.target.value)}
        />
      </div>
      {error ? <p className="text-sm text-rose">{error}</p> : null}
      {done ? (
        <p className="text-sm text-teal">Password updated</p>
      ) : null}
      <Button
        type="button"
        variant="accent"
        size="sm"
        disabled={busy || !password || !confirm}
        onClick={() => void save()}
      >
        {busy ? pendingLabel || "Saving…" : submitLabel}
      </Button>
    </div>
  );
}
