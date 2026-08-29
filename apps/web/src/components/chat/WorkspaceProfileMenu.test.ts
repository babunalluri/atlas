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
    expect(en.common.settings.title).toBe("Settings");
  });

  it("opens settings in a dialog from the gear, not a profile dropdown", () => {
    const source = readFileSync(
      join(dirname(fileURLToPath(import.meta.url)), "WorkspaceProfileMenu.tsx"),
      "utf8",
    );
    expect(source).not.toMatch(/Groww|GrowwTest|Kite/i);
    expect(source).not.toContain("listUserVault");
    expect(source).toContain("settings.title");
    expect(source).toContain("SettingsGearIcon");
    // Settings opens over the surface so a streaming desk is not torn down.
    expect(source).toContain("Modal");
    expect(source).toContain("WorkspaceSettingsBody");
    // Still not a dropdown: a dialog, never a menu.
    expect(source).toContain('aria-haspopup="dialog"');
    expect(source).not.toContain('role="menu"');
    expect(source).not.toContain("LanguageSwitcher");
  });

  it("keeps sign out on the main header bar and language in settings", () => {
    const barSource = readFileSync(
      join(dirname(fileURLToPath(import.meta.url)), "ChatAccountBar.tsx"),
      "utf8",
    );
    expect(barSource).not.toContain("LanguageSwitcher");
    expect(barSource).toContain("signOutFederated");
    expect(barSource).toContain('t("signOut")');

    // The settings content lives in the body component, which both the page
    // and the gear's dialog render.
    const settingsSource = readFileSync(
      join(dirname(fileURLToPath(import.meta.url)), "WorkspaceSettingsBody.tsx"),
      "utf8",
    );
    expect(settingsSource).toContain("LanguageSwitcher");
    expect(settingsSource).toContain("settings.preferences");
    expect(settingsSource).toContain("UserSelfVaultEditor");
    // The body must not reach back for the account bar — that cycle is why it
    // was split out of the page in the first place.
    expect(settingsSource).not.toContain("ChatAccountBar");
  });

  it("lets users overwrite a saved secret without reading the stored value", () => {
    const source = readFileSync(
      join(
        dirname(fileURLToPath(import.meta.url)),
        "../vault/UserSelfVaultEditor.tsx",
      ),
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
