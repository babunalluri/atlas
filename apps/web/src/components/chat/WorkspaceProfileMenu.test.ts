import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import en from "../../../messages/en.json";

describe("workspace profile copy", () => {
  it("keeps user settings generic (not a single-broker form)", () => {
    const hint = en.common.profile.vaultHint;
    expect(hint).toMatch(/access_token/);
    expect(hint).not.toMatch(/groww/i);
    expect(hint).not.toMatch(/kite/i);
    expect(en.common.profile.settings).toBe("Your settings");
  });

  it("does not hardcode broker-only fields in the profile panel", () => {
    const source = readFileSync(
      join(dirname(fileURLToPath(import.meta.url)), "WorkspaceProfileMenu.tsx"),
      "utf8",
    );
    expect(source).not.toMatch(/Groww|GrowwTest|Kite/i);
    expect(source).toContain("listUserVault");
    expect(source).toContain("upsertUserVaultEntry");
  });

  it("lets users overwrite a saved secret without reading the stored value", () => {
    const source = readFileSync(
      join(dirname(fileURLToPath(import.meta.url)), "WorkspaceProfileMenu.tsx"),
      "utf8",
    );
    expect(source).toContain("beginUpdate");
    expect(source).toContain("profile.newValue");
    expect(source).toContain("PencilIcon");
    expect(source).toContain("!editValue.trim()");
    expect(source).not.toMatch(/row\.value|entry\.value/);
    expect(en.common.profile.update).toBe("Update");
    expect(en.common.profile.newValue).toBe("New value");
  });
});
