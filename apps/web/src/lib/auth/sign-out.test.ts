import { describe, expect, it } from "vitest";

import {
  atlasAuthCookieNamesFromHeader,
  atlasSignedOutHomePath,
  cookieExpireAssignment,
  expireAtlasAuthCookies,
  isAtlasAuthCookieName,
} from "@/lib/auth/sign-out";

describe("atlasSignedOutHomePath", () => {
  it("keeps a locale home path", () => {
    expect(atlasSignedOutHomePath("/en")).toBe("/en");
    expect(atlasSignedOutHomePath("/pt-BR")).toBe("/pt-BR");
    expect(atlasSignedOutHomePath("/")).toBe("/");
  });

  it("strips workspace, desk, and admin paths down to the locale home", () => {
    expect(atlasSignedOutHomePath("/en/t/acme/chat")).toBe("/en");
    expect(atlasSignedOutHomePath("/en/admin/agents")).toBe("/en");
    expect(atlasSignedOutHomePath("/t/acme/chat")).toBe("/");
  });

  it("reduces same-origin absolute URLs and rejects unsafe values", () => {
    expect(atlasSignedOutHomePath("http://localhost:3000/en")).toBe("/en");
    expect(atlasSignedOutHomePath("https://app.example/en/t/acme/chat")).toBe(
      "/en",
    );
    expect(atlasSignedOutHomePath("//evil.example/phish")).toBe("/");
    expect(atlasSignedOutHomePath("https://evil.example/en")).toBe("/en");
    expect(atlasSignedOutHomePath("")).toBe("/");
  });
});

describe("Atlas auth cookies", () => {
  it("matches Auth.js session cookies including chunks", () => {
    expect(isAtlasAuthCookieName("authjs.session-token")).toBe(true);
    expect(isAtlasAuthCookieName("authjs.session-token.0")).toBe(true);
    expect(isAtlasAuthCookieName("__Secure-authjs.session-token.1")).toBe(true);
    expect(isAtlasAuthCookieName("next-auth.session-token")).toBe(true);
    expect(isAtlasAuthCookieName("unrelated")).toBe(false);
  });

  it("expires leftover session cookies via Path=/ assignments", () => {
    const header =
      "authjs.session-token.0=aaa; authjs.session-token.1=bbb; other=keep";
    expect(atlasAuthCookieNamesFromHeader(header)).toEqual([
      "authjs.session-token.0",
      "authjs.session-token.1",
    ]);
    const assigned: string[] = [];
    expireAtlasAuthCookies(header, (value) => assigned.push(value));
    expect(assigned).toEqual([
      cookieExpireAssignment("authjs.session-token.0"),
      cookieExpireAssignment("authjs.session-token.1"),
    ]);
  });
});
