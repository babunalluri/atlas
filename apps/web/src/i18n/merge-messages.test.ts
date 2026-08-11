import { describe, expect, it } from "vitest";

import { mergeMessages } from "@/i18n/merge-messages";

describe("mergeMessages", () => {
  it("deep-merges nested namespaces without dropping English fallbacks", () => {
    const en = {
      home: { title: "Atlas", headline: "Hello", support: "English support" },
      nav: { items: { agents: "Agents", teams: "Teams" } },
    };
    const ar = {
      home: { title: "أطلس", headline: "مرحباً" },
      nav: { items: { teams: "الفرق" } },
    };

    const merged = mergeMessages(en, ar) as typeof en;

    expect(merged.home.title).toBe("أطلس");
    expect(merged.home.headline).toBe("مرحباً");
    expect(merged.home.support).toBe("English support");
    expect(merged.nav.items.agents).toBe("Agents");
    expect(merged.nav.items.teams).toBe("الفرق");
  });
});
