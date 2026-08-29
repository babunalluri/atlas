import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

describe("notification inbox", () => {
  it("exposes copy action in the inbox bell", () => {
    const source = readFileSync(
      join(dirname(fileURLToPath(import.meta.url)), "../notifications/NotificationBell.tsx"),
      "utf8",
    );
    expect(source).toContain("formatNotificationForDesk");
    expect(source).toContain("notifications.copy");
    expect(source).toContain("CopyGlyph");
    expect(source).not.toContain("useInLiveTrading");
    expect(source).not.toContain("applyToLiveTrading");
  });

  it("wires desk draft provider on the trader workspace", () => {
    // The three-tab desk is gone; the workspace is the surface that hosts the
    // chat rail, so it is what must provide the draft context.
    const source = readFileSync(
      join(
        dirname(fileURLToPath(import.meta.url)),
        "../domains/TraderWorkspace.tsx",
      ),
      "utf8",
    );
    expect(source).toContain("DeskChatDraftProvider");
    expect(source).toContain("chat_targets");
    expect(source).toContain("WorkspaceDeskChat");
  });
});
