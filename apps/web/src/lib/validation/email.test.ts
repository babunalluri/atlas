import { describe, expect, it } from "vitest";

import {
  EMAIL_ALREADY_IN_USE,
  isTakenEmail,
} from "@/lib/validation/email";

describe("isTakenEmail", () => {
  it("treats emails as unique ignoring case and surrounding space", () => {
    expect(isTakenEmail(" Babu@Acme.test ", ["babu@acme.test"])).toBe(true);
    expect(isTakenEmail("other@acme.test", ["babu@acme.test"])).toBe(false);
    expect(isTakenEmail("", ["babu@acme.test"])).toBe(false);
  });

  it("exposes the same message the API returns", () => {
    expect(EMAIL_ALREADY_IN_USE).toBe("This email is already in use.");
  });
});
