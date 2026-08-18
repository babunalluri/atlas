import { describe, expect, it } from "vitest";

import { formatNotificationForDesk } from "@/components/domains/desk-chat-draft";

describe("formatNotificationForDesk", () => {
  it("uses body when title differs (e.g. admin label + trade line)", () => {
    expect(
      formatNotificationForDesk({
        title: "Test",
        body: "BUY SBI",
      }),
    ).toBe("BUY SBI");
  });

  it("uses body alone when it matches the title", () => {
    expect(
      formatNotificationForDesk({
        title: "BUY signal",
        body: "BUY signal",
      }),
    ).toBe("BUY signal");
  });

  it("uses signal body without repeating the title", () => {
    expect(
      formatNotificationForDesk({
        title: "New trading signal",
        body: "BUY NIFTY 24500 CE · ADX pass · PCR 1.02",
      }),
    ).toBe("BUY NIFTY 24500 CE · ADX pass · PCR 1.02");
  });

  it("falls back to title when body is empty", () => {
    expect(
      formatNotificationForDesk({
        title: "Desk update",
        body: "   ",
      }),
    ).toBe("Desk update");
  });
});
