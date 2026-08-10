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

describe("provisionalSlug", () => {
  it("uses a short random suffix instead of Date.now()", async () => {
    const { provisionalSlug, isProvisionalSlug } = await import(
      "@/lib/validation/agent-form"
    );
    const slug = provisionalSlug("team");
    expect(slug.startsWith("team-")).toBe(true);
    expect(slug.length).toBeLessThan(20);
    expect(/\d{10,}/.test(slug)).toBe(false);
    expect(isProvisionalSlug(slug)).toBe(true);
    expect(isProvisionalSlug("untitled-team-1784568920294")).toBe(true);
    expect(isProvisionalSlug("support-concierge")).toBe(false);
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
    frameworkAdapter: "agno",
    guardrails: {
      promptInjection: false,
      piiDetection: false,
      openaiModeration: false,
    },
  };

  it("accepts a complete draft", () => {
    const result = validateAgentDraft(valid);
    expect(result.success).toBe(true);
  });

  it("preserves tenant tool definitionId on save validation", () => {
    const result = validateAgentDraft({
      ...valid,
      tools: [
        {
          id: "definition_abc",
          kind: "rest_mutate",
          label: "Freshdesk",
          enabled: true,
          config: {},
          requiresApproval: true,
          definitionId: "65ee3f68-fab0-491e-b020-bf3deffc3a55",
        },
      ],
    });
    expect(result.success).toBe(true);
    if (result.success) {
      expect(result.data.tools[0]?.definitionId).toBe(
        "65ee3f68-fab0-491e-b020-bf3deffc3a55",
      );
    }
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
