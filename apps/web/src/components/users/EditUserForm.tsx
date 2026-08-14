"use client";

import { useState } from "react";

import { Button } from "@/components/ui/Button";
import { FieldError, FieldHint, Input, Label, Select } from "@/components/ui/Field";
import type { TenantUser, TenantUserInput } from "@/lib/api/types";
import { formatApiError } from "@/lib/agentos/client";
import { passwordError } from "@/components/users/SetPasswordForm";
import {
  EMAIL_ALREADY_IN_USE,
  isTakenEmail,
} from "@/lib/validation/email";

export function EditUserForm({
  user,
  takenEmails = [],
  onSubmit,
  onCancel,
}: {
  user: TenantUser;
  takenEmails?: string[];
  onSubmit: (values: {
    displayName: string;
    email: string;
    role: TenantUserInput["role"];
    password?: string;
    passwordConfirm?: string;
  }) => Promise<void>;
  onCancel: () => void;
}) {
  const [displayName, setDisplayName] = useState(user.displayName);
  const [email, setEmail] = useState(user.email ?? "");
  const [role, setRole] = useState<TenantUserInput["role"]>(user.role);
  const [password, setPassword] = useState("");
  const [passwordConfirm, setPasswordConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const canSetPassword = !user.invitePending && !user.userId.startsWith("pending:") && !user.userId.startsWith("invite:");

  async function save() {
    if (!displayName.trim()) {
      setError("Display name is required");
      return;
    }
    if (!email.trim() || !email.includes("@")) {
      setError("A valid email is required");
      return;
    }
    if (
      email.trim().toLowerCase() !== (user.email ?? "").trim().toLowerCase() &&
      isTakenEmail(email, takenEmails)
    ) {
      setError(EMAIL_ALREADY_IN_USE);
      return;
    }
    if (password || passwordConfirm) {
      const mismatch = passwordError(password, passwordConfirm);
      if (mismatch) {
        setError(mismatch);
        return;
      }
    }
    setBusy(true);
    setError(null);
    try {
      await onSubmit({
        displayName: displayName.trim(),
        email: email.trim(),
        role,
        ...(password
          ? { password, passwordConfirm }
          : {}),
      });
    } catch (reason) {
      setError(formatApiError(reason, "Save failed"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-3">
      <div className="grid gap-2.5 sm:grid-cols-2">
        <div>
          <Label htmlFor="edit-display-name">Display name</Label>
          <Input
            id="edit-display-name"
            value={displayName}
            autoComplete="name"
            onChange={(event) => setDisplayName(event.target.value)}
          />
        </div>
        <div>
          <Label htmlFor="edit-email">Email</Label>
          <Input
            id="edit-email"
            type="email"
            value={email}
            autoComplete="off"
            disabled={Boolean(user.invitePending)}
            onChange={(event) => setEmail(event.target.value)}
          />
          {error && /email/i.test(error) ? <FieldError message={error} /> : null}
        </div>
        <div className="sm:col-span-2">
          <Label htmlFor="edit-role">Role</Label>
          <Select
            id="edit-role"
            value={role}
            onChange={(event) =>
              setRole(event.target.value as TenantUserInput["role"])
            }
          >
            <option value="end_user">End user</option>
            <option value="tenant_admin">Tenant admin</option>
          </Select>
        </div>
        {canSetPassword ? (
          <div className="grid grid-cols-2 gap-2.5 sm:col-span-2">
            <div className="min-w-0">
              <Label htmlFor="edit-password">New password</Label>
              <Input
                id="edit-password"
                type="password"
                autoComplete="new-password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
              />
              <FieldHint>
                Password is optional. Leave blank to keep the current password.
              </FieldHint>
            </div>
            <div className="min-w-0">
              <Label htmlFor="edit-password-confirm">Confirm password</Label>
              <Input
                id="edit-password-confirm"
                type="password"
                autoComplete="new-password"
                value={passwordConfirm}
                onChange={(event) => setPasswordConfirm(event.target.value)}
              />
            </div>
          </div>
        ) : null}
      </div>
      {error ? (
        <p role="alert" className="text-sm text-rose">
          {error}
        </p>
      ) : null}
      <div className="flex justify-end gap-2">
        <Button
          type="button"
          variant="secondary"
          size="sm"
          disabled={busy}
          onClick={onCancel}
        >
          Cancel
        </Button>
        <Button
          type="button"
          variant="accent"
          size="sm"
          disabled={busy}
          onClick={() => void save()}
        >
          {busy ? "Saving…" : "Save"}
        </Button>
      </div>
    </div>
  );
}
