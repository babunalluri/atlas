import { describe, expect, it } from "vitest";

import { buildEmbedPaths, buildEmbedSnippets } from "./snippets";

describe("buildEmbedPaths", () => {
  it("builds team and workflow paths", () => {
    expect(buildEmbedPaths("acme", "team", "support")).toEqual({
      chatPath: "/t/acme/teams/support",
      embedPath: "/embed/acme/team/support",
    });
    expect(buildEmbedPaths("acme", "workflow", "intake")).toEqual({
      chatPath: "/t/acme/workflows/intake",
      embedPath: "/embed/acme/workflow/intake",
    });
  });
});

describe("buildEmbedSnippets", () => {
  it("includes hosted link, iframe, and script for the embed URL", () => {
    const snippets = buildEmbedSnippets(
      "acme",
      "team",
      "support",
      "https://app.example",
    );
    expect(snippets.chatUrl).toBe("https://app.example/t/acme/teams/support");
    expect(snippets.embedUrl).toBe(
      "https://app.example/embed/acme/team/support",
    );
    expect(snippets.iframe).toContain(snippets.embedUrl);
    expect(snippets.script).toContain(snippets.embedUrl);
    expect(snippets.emailAddress).toBeNull();
  });

  it("builds inbound email address when domain is configured", () => {
    const snippets = buildEmbedSnippets(
      "acme",
      "team",
      "support",
      "https://app.example",
      "inbound.example.com",
    );
    expect(snippets.emailAddress).toBe(
      "team-acme.support@inbound.example.com",
    );
  });
});
