"use client";

import { useState } from "react";

import { AdminFormDialog } from "@/components/ui/AdminFormDialog";
import { Button } from "@/components/ui/Button";
import { FieldHint, Input, Label } from "@/components/ui/Field";
import { TimezoneSelect } from "@/components/ui/TimezoneSelect";
import { passwordError } from "@/components/users/SetPasswordForm";
import { updatePlatformTenant } from "@/lib/api/admin";
import type { PlatformTenant } from "@/lib/api/types";
import { formatApiError } from "@/lib/agentos/client";
import {
  EMAIL_ALREADY_IN_USE,
  isTakenEmail,
} from "@/lib/validation/email";

function domainLabel(domain: string): string {
  if (domain === "stock_broker") return "Stock Broker";
  if (domain === "dental_clinic") return "Dental Clinic";
  return "General";
}

export function EditTenantDialog({
  tenant,
  getAccessToken,
  takenEmails = [],
  onSaved,
  onClose,
}: {
  tenant: PlatformTenant;
  getAccessToken: () => Promise<string>;
  takenEmails?: string[];
  onSaved: (tenant: PlatformTenant) => void;
  onClose: () => void;
}) {
  const hasOwner = Boolean(tenant.ownerEmail);
  const [name, setName] = useState(tenant.name);
  const [timezone, setTimezone] = useState(tenant.timezone || "UTC");
  const [ownerEmail, setOwnerEmail] = useState(tenant.ownerEmail ?? "");
  const [ownerPassword, setOwnerPassword] = useState("");
  const [ownerPasswordConfirm, setOwnerPasswordConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const normalizedEmail = ownerEmail.trim().toLowerCase();
  const currentEmail = (tenant.ownerEmail ?? "").toLowerCase();
  const creatingOwner =
    (!hasOwner && Boolean(normalizedEmail)) ||
    (hasOwner && Boolean(normalizedEmail) && normalizedEmail !== currentEmail);
  const passwordRequired = creatingOwner;
  const saveDisabled =
    busy ||
    !name.trim() ||
    (passwordRequired && (!ownerPassword || !ownerPasswordConfirm));

  async function save() {
    if (creatingOwner && !normalizedEmail) {
      setError("Owner email is required when creating an owner");
      return;
    }
    if (
      normalizedEmail &&
      normalizedEmail !== currentEmail &&
      isTakenEmail(normalizedEmail, takenEmails)
    ) {
      setError(EMAIL_ALREADY_IN_USE);
      return;
    }
    if (creatingOwner || ownerPassword || ownerPasswordConfirm) {
      const mismatch = passwordError(ownerPassword, ownerPasswordConfirm);
      if (mismatch && (creatingOwner || ownerPassword || ownerPasswordConfirm)) {
        setError(mismatch);
        return;
      }
    }
    setBusy(true);
    setError(null);
    try {
      const updated = await updatePlatformTenant(await getAccessToken(), tenant.id, {
        name: name.trim(),
        timezone,
        ownerEmail: normalizedEmail || undefined,
        ownerPassword: ownerPassword || undefined,
        ownerPasswordConfirm: ownerPassword ? ownerPasswordConfirm : undefined,
      });
      onSaved(updated);
      onClose();
    } catch (reason) {
      setError(
        formatApiError(reason, "Could not update tenant"),
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <AdminFormDialog
      title="Edit tenant"
      subtitle={
        hasOwner
          ? `Update ${tenant.name}. Password is optional. Leave blank to keep the current password.`
          : "This tenant has no org owner. Add email and password so someone can sign in."
      }
      titleId="edit-tenant-title"
      onClose={onClose}
    >
      <div className="space-y-3">
        <div className="grid gap-2.5 sm:grid-cols-2">
          <div>
            <Label htmlFor="edit-tenant-name">Name</Label>
            <Input
              id="edit-tenant-name"
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
          </div>
          <TimezoneSelect
            id="edit-tenant-timezone"
            value={timezone}
            onChange={setTimezone}
          />
          <div>
            <Label htmlFor="edit-tenant-slug">Slug</Label>
            <Input id="edit-tenant-slug" value={tenant.slug} disabled readOnly />
          </div>
          <div>
            <Label htmlFor="edit-tenant-domain">Industry domain</Label>
            <Input
              id="edit-tenant-domain"
              value={domainLabel(tenant.domain)}
              disabled
              readOnly
            />
          </div>
          <div className="sm:col-span-2">
            <Label htmlFor="edit-tenant-org">Organization ID</Label>
            <Input
              id="edit-tenant-org"
              value={tenant.authOrgId}
              disabled
              readOnly
            />
          </div>
          <div className="sm:col-span-2">
            <Label htmlFor="edit-owner-email">Owner email</Label>
            <Input
              id="edit-owner-email"
              type="email"
              autoComplete="off"
              value={ownerEmail}
              placeholder="owner@acme.test"
              onChange={(event) => setOwnerEmail(event.target.value)}
            />
          </div>
          <div className="grid grid-cols-2 gap-2.5 sm:col-span-2">
            <div className="min-w-0">
              <Label htmlFor="edit-owner-password">
                {hasOwner && !creatingOwner ? "New password" : "Owner password"}
              </Label>
              <Input
                id="edit-owner-password"
                type="password"
                autoComplete="new-password"
                value={ownerPassword}
                onChange={(event) => setOwnerPassword(event.target.value)}
              />
              <FieldHint>
                {passwordRequired
                  ? "At least 8 characters"
                  : "Optional · at least 8 characters"}
              </FieldHint>
            </div>
            <div className="min-w-0">
              <Label htmlFor="edit-owner-password-confirm">
                Confirm password
              </Label>
              <Input
                id="edit-owner-password-confirm"
                type="password"
                autoComplete="new-password"
                value={ownerPasswordConfirm}
                onChange={(event) => setOwnerPasswordConfirm(event.target.value)}
              />
            </div>
          </div>
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
            onClick={onClose}
          >
            Cancel
          </Button>
          <Button
            type="button"
            variant="accent"
            size="sm"
            disabled={saveDisabled}
            onClick={() => void save()}
          >
            {busy ? "Saving…" : "Save"}
          </Button>
        </div>
      </div>
    </AdminFormDialog>
  );
}
