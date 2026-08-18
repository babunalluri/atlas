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

  it("wires desk draft provider on the customer trading desk", () => {
    const source = readFileSync(
      join(
        dirname(fileURLToPath(import.meta.url)),
        "../domains/StockBrokerCustomerDesk.tsx",
      ),
      "utf8",
    );
    expect(source).toContain("DeskChatDraftProvider");
    expect(source).toContain("chat_targets");
  });
});
