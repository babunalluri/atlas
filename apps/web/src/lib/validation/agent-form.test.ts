import { describe, expect, it } from "vitest";

import {
  slugifyName,
  validateAgentDraft,
} from "@/lib/validation/agent-form";

describe("slugifyName", () => {
  it("normalizes display names", () => {
    expect(slugifyName("Claims Navigator!")).toBe("claims-navigator");
  });
});

describe("validateAgentDraft", () => {
  const valid = {
    name: "Support Concierge",
    slug: "support-concierge",
    description: "Helps customers",
    instructions:
      "You are a careful support agent that cites knowledge sources.",
    model: "claude-sonnet-4",
    temperature: 0.2,
    memoryMode: "session",
    tools: [
      {
        id: "tool_web",
        kind: "web_search",
        label: "Web search",
        enabled: true,
        config: {},
        requiresApproval: false,
      },
    ],
    knowledgeBaseId: null,
  };

  it("accepts a complete draft", () => {
    const result = validateAgentDraft(valid);
    expect(result.success).toBe(true);
  });

  it("rejects short instructions and bad slugs", () => {
    const result = validateAgentDraft({
      ...valid,
      instructions: "too short",
      slug: "Bad Slug",
    });
    expect(result.success).toBe(false);
    if (!result.success) {
      const paths = result.error.issues.map((i) => i.path[0]);
      expect(paths).toContain("instructions");
      expect(paths).toContain("slug");
    }
  });

  it("rejects out-of-range temperature", () => {
    const result = validateAgentDraft({ ...valid, temperature: 3 });
    expect(result.success).toBe(false);
  });
});
