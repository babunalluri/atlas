import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next-auth/react", () => ({
  signOut: vi.fn(async () => undefined),
}));

import { signOut } from "next-auth/react";

import { signOutFederated } from "@/lib/auth/federated-signout";

describe("signOutFederated", () => {
  const replace = vi.fn();

  beforeEach(() => {
    vi.mocked(signOut).mockClear();
    replace.mockClear();
    vi.stubGlobal("window", { location: { replace } });
    vi.stubGlobal("document", { cookie: "" });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("calls NextAuth signOut then hard-navigates to the signed-out locale home", async () => {
    await signOutFederated("/en/t/acme/chat");
    expect(signOut).toHaveBeenCalledWith({
      redirect: false,
      callbackUrl: "/en",
    });
    expect(replace).toHaveBeenCalledWith("/en");
  });
});
