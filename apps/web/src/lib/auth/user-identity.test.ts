import { describe, expect, it } from "vitest";

import {
  composeDisplayName,
  userDisplayName,
  userIdentityTitle,
  userInitials,
} from "@/lib/auth/user-identity";

describe("userDisplayName", () => {
  it("prefers the first token of the session display name", () => {
    expect(
      userDisplayName({ name: "Babu", email: "babu@atlas.ai" }),
    ).toBe("Babu");
  });

  it("shows only the given name for a multi-word display name", () => {
    expect(userDisplayName({ name: "Babu Nalluri" })).toBe("Babu");
  });

  it("does not repeat a copied family name", () => {
    expect(userDisplayName({ name: "Babu Babu" })).toBe("Babu");
  });

  it("falls back to email when name is missing", () => {
    expect(userDisplayName({ email: "babu@atlas.ai" })).toBe("babu@atlas.ai");
  });
});

describe("userInitials", () => {
  it("uses the first letter of a single given name", () => {
    expect(userInitials({ name: "Babu", email: "babu@atlas.ai" })).toBe("B");
  });

  it("uses only the given-name initial for multi-word names", () => {
    expect(userInitials({ name: "Babu Nalluri" })).toBe("B");
  });

  it("uses a single initial when given and family were copied", () => {
    expect(userInitials({ name: "Babu Babu" })).toBe("B");
  });

  it("uses the email local-part when there is no name", () => {
    expect(userInitials({ email: "babu@atlas.ai" })).toBe("B");
  });
});

describe("userIdentityTitle", () => {
  it("combines first name and email for the tooltip", () => {
    expect(
      userIdentityTitle({ name: "Babu Nalluri", email: "babu@atlas.ai" }),
    ).toBe("Babu · babu@atlas.ai");
  });
});

describe("composeDisplayName", () => {
  it("prefers a display name over joining given and family", () => {
    expect(
      composeDisplayName({
        name: "Babu",
        givenName: "Babu",
        familyName: "Babu",
      }),
    ).toBe("Babu");
  });

  it("does not concatenate identical given and family names", () => {
    expect(
      composeDisplayName({ givenName: "Babu", familyName: "Babu" }),
    ).toBe("Babu");
  });
});
