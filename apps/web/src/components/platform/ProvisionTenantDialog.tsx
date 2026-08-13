"use client";

import { useState } from "react";

import { AdminFormDialog } from "@/components/ui/AdminFormDialog";
import { Button } from "@/components/ui/Button";
import { FieldHint, Input, Label } from "@/components/ui/Field";
import { SearchableSelect } from "@/components/ui/SearchableSelect";
import {
  TimezoneSelect,
  browserTimezone,
} from "@/components/ui/TimezoneSelect";
import { passwordError } from "@/components/users/SetPasswordForm";
import { createPlatformTenant } from "@/lib/api/admin";
import type { PlatformTenant, WorkspaceDomain } from "@/lib/api/types";
import { formatApiError } from "@/lib/agentos/client";
import {
  EMAIL_ALREADY_IN_USE,
  isTakenEmail,
} from "@/lib/validation/email";
import { slugifyName } from "@/lib/validation/agent-form";

export function ProvisionTenantDialog({
  getAccessToken,
  takenEmails = [],
  onCreated,
  onClose,
}: {
  getAccessToken: () => Promise<string>;
  takenEmails?: string[];
  onCreated: (tenant: PlatformTenant) => void;
  onClose: () => void;
}) {
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [slugTouched, setSlugTouched] = useState(false);
  const [authOrgId, setAuthOrgId] = useState("");
  const [timezone, setTimezone] = useState(browserTimezone);
  const [domain, setDomain] = useState<WorkspaceDomain>("generic");
  const [ownerEmail, setOwnerEmail] = useState("");
  const [ownerPassword, setOwnerPassword] = useState("");
  const [ownerPasswordConfirm, setOwnerPasswordConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const saveDisabled =
    busy ||
    !name.trim() ||
    !authOrgId.trim() ||
    !ownerEmail.trim() ||
    !ownerPassword ||
    !ownerPasswordConfirm;

  async function provision() {
    if (!name.trim() || !authOrgId.trim() || !ownerEmail.trim()) return;
    if (isTakenEmail(ownerEmail, takenEmails)) {
      setError(EMAIL_ALREADY_IN_USE);
      return;
    }
    const mismatch = passwordError(ownerPassword, ownerPasswordConfirm);
    if (mismatch) {
      setError(mismatch);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const created = await createPlatformTenant(await getAccessToken(), {
        name: name.trim(),
        slug: slugifyName(slug || name),
        authOrgId: authOrgId.trim(),
        timezone,
        domain,
        ownerEmail: ownerEmail.trim(),
        ownerPassword,
        ownerPasswordConfirm,
      });
      onCreated(created);
      onClose();
    } catch (reason) {
      setError(
        formatApiError(reason, "Tenant creation failed"),
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <AdminFormDialog
      title="Provision tenant"
      subtitle="Atlas creates the Keycloak org group and owner account. Passwords stay in Keycloak — Atlas never stores them."
      titleId="provision-tenant-title"
      onClose={onClose}
    >
      <div className="space-y-3">
        <div className="grid gap-2.5 sm:grid-cols-2">
          <div>
            <Label htmlFor="provision-tenant-name">Name</Label>
            <Input
              id="provision-tenant-name"
              value={name}
              placeholder="Acme Corp"
              onChange={(event) => {
                const next = event.target.value;
                setName(next);
                if (!slugTouched) setSlug(slugifyName(next));
              }}
            />
          </div>
          <div>
            <Label htmlFor="provision-tenant-slug">Slug</Label>
            <Input
              id="provision-tenant-slug"
              value={slug}
              placeholder="acme"
              onChange={(event) => {
                setSlugTouched(true);
                setSlug(slugifyName(event.target.value));
              }}
            />
          </div>
          <div className="sm:col-span-2">
            <Label
              htmlFor="provision-org-id"
              hint="Must match the signed-in organization id"
            >
              Organization ID
            </Label>
            <Input
              id="provision-org-id"
              value={authOrgId}
              placeholder="org_..."
              onChange={(event) => setAuthOrgId(event.target.value)}
            />
          </div>
          <div>
            <Label htmlFor="provision-tenant-domain">Industry domain</Label>
            <SearchableSelect
              id="provision-tenant-domain"
              value={domain}
              onChange={(value) => setDomain(value as WorkspaceDomain)}
              placeholder="Select domain"
              options={[
                { value: "generic", label: "General" },
                { value: "stock_broker", label: "Stock Broker" },
                { value: "dental_clinic", label: "Dental Clinic" },
              ]}
            />
          </div>
          <TimezoneSelect
            id="provision-tenant-timezone"
            value={timezone}
            onChange={setTimezone}
          />
          <div className="sm:col-span-2">
            <Label htmlFor="provision-owner-email">Owner email</Label>
            <Input
              id="provision-owner-email"
              type="email"
              autoComplete="off"
              value={ownerEmail}
              placeholder="owner@acme.test"
              onChange={(event) => setOwnerEmail(event.target.value)}
            />
          </div>
          <div className="grid grid-cols-2 gap-2.5 sm:col-span-2">
            <div className="min-w-0">
              <Label htmlFor="provision-owner-password">Owner password</Label>
              <Input
                id="provision-owner-password"
                type="password"
                autoComplete="new-password"
                value={ownerPassword}
                onChange={(event) => setOwnerPassword(event.target.value)}
              />
              <FieldHint>At least 8 characters</FieldHint>
            </div>
            <div className="min-w-0">
              <Label htmlFor="provision-owner-password-confirm">
                Confirm password
              </Label>
              <Input
                id="provision-owner-password-confirm"
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
            onClick={() => void provision()}
          >
            {busy ? "Provisioning…" : "Provision tenant"}
          </Button>
        </div>
      </div>
    </AdminFormDialog>
  );
}
