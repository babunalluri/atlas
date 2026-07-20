"use client";

import { useEffect, useState } from "react";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Input, Label } from "@/components/ui/Field";
import {
  createPlatformSandboxPackage,
  listPlatformSandboxPackages,
  updatePlatformSandboxPackage,
} from "@/lib/api/admin";
import type { SandboxPythonPackage } from "@/lib/api/types";
import { useAgentOsToken } from "@/lib/auth/token";

export function SandboxPackagesPanel() {
  const { getAccessToken } = useAgentOsToken();
  const [rows, setRows] = useState<SandboxPythonPackage[]>([]);
  const [banner, setBanner] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [name, setName] = useState("");
  const [version, setVersion] = useState("");
  const [sha256, setSha256] = useState("");

  async function reload() {
    const token = await getAccessToken();
    setRows(await listPlatformSandboxPackages(token));
  }

  useEffect(() => {
    void reload().catch((error: unknown) => {
      setBanner(error instanceof Error ? error.message : "Failed to load packages");
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function addPackage() {
    setBusy(true);
    setBanner(null);
    try {
      const token = await getAccessToken();
      await createPlatformSandboxPackage(token, {
        name: name.trim().toLowerCase(),
        version: version.trim(),
        sha256: sha256.trim(),
        active: true,
      });
      setName("");
      setVersion("");
      setSha256("");
      await reload();
      setBanner(
        "Package added. Rebuild atlas-sandbox-python with the updated allowlist before tenants can import it at runtime.",
      );
    } catch (error) {
      setBanner(error instanceof Error ? error.message : "Create failed");
    } finally {
      setBusy(false);
    }
  }

  async function setActive(row: SandboxPythonPackage, active: boolean) {
    setBusy(true);
    setBanner(null);
    try {
      const token = await getAccessToken();
      await updatePlatformSandboxPackage(token, row.id, { active });
      await reload();
    } catch (error) {
      setBanner(error instanceof Error ? error.message : "Update failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto max-w-4xl space-y-5">
      <div>
        <p className="text-xs font-semibold uppercase tracking-[0.14em] text-teal">
          Platform
        </p>
        <h1 className="font-display text-3xl font-semibold">Sandbox packages</h1>
        <p className="mt-2 text-sm text-slate-muted">
          Allowlist pins for editable Python sandboxes. Adding a package updates
          the catalog; install it into the sandbox image before tenants can import
          it.
        </p>
      </div>

      {banner ? (
        <div className="rounded-lg border border-teal/30 bg-teal/10 px-3 py-2 text-sm">
          {banner}
        </div>
      ) : null}

      <section className="surface-panel rounded-2xl p-5">
        <h2 className="font-display text-lg font-semibold">Add package</h2>
        <div className="mt-4 grid gap-4 md:grid-cols-3">
          <div>
            <Label htmlFor="pkg-name">Name</Label>
            <Input
              id="pkg-name"
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="jsonschema"
            />
          </div>
          <div>
            <Label htmlFor="pkg-version">Version</Label>
            <Input
              id="pkg-version"
              value={version}
              onChange={(event) => setVersion(event.target.value)}
              placeholder="4.26.0"
            />
          </div>
          <div>
            <Label htmlFor="pkg-sha">SHA-256</Label>
            <Input
              id="pkg-sha"
              value={sha256}
              onChange={(event) => setSha256(event.target.value)}
              placeholder="64 hex chars"
            />
          </div>
        </div>
        <div className="mt-4">
          <Button
            variant="accent"
            size="sm"
            disabled={busy || !name || !version || sha256.length !== 64}
            onClick={addPackage}
          >
            Add to allowlist
          </Button>
        </div>
      </section>

      <section className="surface-panel rounded-2xl p-5">
        <h2 className="font-display text-lg font-semibold">Allowlist</h2>
        <ul className="mt-4 space-y-3">
          {rows.map((row) => (
            <li
              key={row.id}
              className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-line px-3 py-3"
            >
              <div>
                <p className="text-sm font-semibold">
                  {row.name}=={row.version}
                </p>
                <p className="font-mono text-xs text-slate-muted">{row.sha256}</p>
              </div>
              <div className="flex items-center gap-2">
                <Badge tone={row.active ? "success" : "neutral"}>
                  {row.active ? "active" : "disabled"}
                </Badge>
                <Button
                  variant="secondary"
                  size="sm"
                  disabled={busy}
                  onClick={() => setActive(row, !row.active)}
                >
                  {row.active ? "Disable" : "Enable"}
                </Button>
              </div>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
