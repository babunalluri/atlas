import { describe, expect, it } from "vitest";

import { buildEmbedPaths, buildEmbedSnippets } from "./snippets";

describe("buildEmbedPaths", () => {
  it("builds agent chat and embed paths", () => {
    expect(buildEmbedPaths("acme", "agent", "tweee")).toEqual({
      chatPath: "/t/acme/chat/tweee",
      embedPath: "/embed/acme/agent/tweee",
    });
  });

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
      "agent",
      "tweee",
      "https://app.example",
    );
    expect(snippets.chatUrl).toBe("https://app.example/t/acme/chat/tweee");
    expect(snippets.embedUrl).toBe(
      "https://app.example/embed/acme/agent/tweee",
    );
    expect(snippets.iframe).toContain(snippets.embedUrl);
    expect(snippets.script).toContain(snippets.embedUrl);
  });
});
