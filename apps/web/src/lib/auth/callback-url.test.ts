import { describe, expect, it } from "vitest";

import { safeAuthCallbackUrl } from "@/lib/auth/callback-url";

describe("safeAuthCallbackUrl", () => {
  it("keeps relative deep links", () => {
    expect(safeAuthCallbackUrl("/admin/users")).toBe("/admin/users");
    expect(safeAuthCallbackUrl("/admin/workflows?x=1")).toBe(
      "/admin/workflows?x=1",
    );
  });

  it("rejects absolute or protocol-relative URLs", () => {
    expect(safeAuthCallbackUrl("https://evil.example/phish")).toBe("/admin");
    expect(safeAuthCallbackUrl("//evil.example/phish")).toBe("/admin");
    expect(safeAuthCallbackUrl(null)).toBe("/admin");
  });
});
