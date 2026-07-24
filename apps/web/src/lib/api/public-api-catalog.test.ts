import { describe, expect, it } from "vitest";

import {
  buildPublicApiRunCatalog,
  formatPublicApiRunError,
  publicApiCatalogEmptyHint,
  publicApiTeamEmptyLabel,
  publicApiWorkflowTeamHint,
} from "@/lib/api/public-api-catalog";

describe("buildPublicApiRunCatalog", () => {
  it("includes published workflows that have team steps", () => {
    const load = buildPublicApiRunCatalog([
      {
        id: "wf-1",
        name: "Onboarding",
        slug: "onboarding",
        updated_at: "2026-07-20T00:00:00Z",
        published: {
          version: 2,
          mode: "sequential",
          steps: [
            {
              name: "Front line",
              target_type: "team",
              target_config_id: "team-1",
              target_name: "Support",
              target_slug: "support",
            },
            {
              name: "Agent step",
              target_type: "agent",
              target_config_id: "agent-1",
              target_name: "Bot",
              target_slug: "bot",
            },
          ],
        },
      },
    ]);

    expect(load.publishedCount).toBe(1);
    expect(load.rows).toHaveLength(1);
    expect(load.rows[0]?.workflow.name).toBe("Onboarding");
    expect(load.rows[0]?.teamsFromWorkflowSteps).toBe(true);
    expect(load.rows[0]?.teams).toEqual([
      {
        id: "team-1",
        name: "Support",
        slug: "support",
        stepName: "Front line",
      },
    ]);
    expect(publicApiCatalogEmptyHint(load)).toBe("");
    expect(publicApiWorkflowTeamHint(load.rows[0])).toBe("");
  });

  it("includes published agent-only workflows and falls back to published teams", () => {
    const load = buildPublicApiRunCatalog(
      [
        {
          id: "wf-draft",
          name: "Draft only",
          slug: "draft-only",
          updated_at: "2026-07-20T00:00:00Z",
          published: null,
        },
        {
          id: "wf-agent",
          name: "Smoke Onboarding",
          slug: "smoke-onboarding",
          updated_at: "2026-07-20T00:00:00Z",
          published: {
            version: 1,
            mode: "sequential",
            steps: [
              {
                name: "Handle request",
                target_type: "agent",
                target_config_id: "agent-1",
                target_name: "Support Concierge",
                target_slug: "support-concierge",
              },
            ],
          },
        },
      ],
      [{ id: "team-pub", name: "Ops", slug: "ops" }],
    );

    expect(load.publishedCount).toBe(1);
    expect(load.publishedTeamCount).toBe(1);
    expect(load.rows).toHaveLength(1);
    expect(load.rows[0]?.workflow.name).toBe("Smoke Onboarding");
    expect(load.rows[0]?.teamsFromWorkflowSteps).toBe(false);
    expect(load.rows[0]?.teams).toEqual([
      { id: "team-pub", name: "Ops", slug: "ops", stepName: "Ops" },
    ]);
    expect(publicApiCatalogEmptyHint(load)).toBe("");
    expect(publicApiWorkflowTeamHint(load.rows[0])).toContain("no team steps");
    expect(publicApiTeamEmptyLabel(load.rows[0])).toBe("");
  });

  it("shows a clear empty team state when nothing is published as a team", () => {
    const load = buildPublicApiRunCatalog(
      [
        {
          id: "wf-agent",
          name: "Smoke Onboarding",
          slug: "smoke-onboarding",
          updated_at: "2026-07-20T00:00:00Z",
          published: {
            version: 1,
            mode: "sequential",
            steps: [
              {
                name: "Handle request",
                target_type: "agent",
                target_config_id: "agent-1",
              },
            ],
          },
        },
      ],
      [],
    );

    expect(load.rows[0]?.teams).toEqual([]);
    expect(publicApiTeamEmptyLabel(load.rows[0])).toContain(
      "No published teams",
    );
    expect(publicApiWorkflowTeamHint(load.rows[0])).toContain(
      "no published teams",
    );
  });

  it("explains when nothing is published", () => {
    const load = buildPublicApiRunCatalog([
      {
        id: "wf-draft",
        name: "Draft only",
        slug: "draft-only",
        updated_at: "2026-07-20T00:00:00Z",
        published: null,
      },
    ]);
    expect(load.rows).toEqual([]);
    expect(publicApiCatalogEmptyHint(load)).toContain("No published workflows");
    expect(publicApiCatalogEmptyHint(load)).not.toContain("team step");
  });

  it("formats public run errors for non-step teams", () => {
    expect(
      formatPublicApiRunError(
        'HTTP 404: {"detail":"Team is not a step in this published workflow"}',
      ),
    ).toContain("not a step on the selected published workflow");
  });
});
