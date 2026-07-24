import type { WorkflowMode, WorkflowSummary } from "@/lib/api/types";

export type PublicApiTeamOption = {
  id: string;
  name: string;
  slug: string;
  /** Workflow step name when the team is a step; otherwise mirrors name. */
  stepName: string;
};

export type PublicApiCatalogRow = {
  workflow: WorkflowSummary;
  teams: PublicApiTeamOption[];
  /** True when `teams` came from published workflow steps (valid for public runs). */
  teamsFromWorkflowSteps: boolean;
};

export type PublicApiCatalogLoad = {
  /** All published workflows (including agent-only). */
  rows: PublicApiCatalogRow[];
  publishedCount: number;
  publishedTeamCount: number;
};

/** Minimal workflow list shape used to build the Public API try-it catalog. */
export type PublicApiWorkflowRowInput = {
  id: string;
  name: string;
  slug: string;
  updated_at: string;
  published: {
    version: number;
    mode: WorkflowMode;
    steps: Array<{
      name: string;
      target_type: string;
      target_config_id: string;
      target_name?: string | null;
      target_slug?: string | null;
    }>;
  } | null;
};

export type PublicApiPublishedTeamInput = {
  id: string;
  name: string;
  slug: string;
};

export function teamStepsFromPublished(
  published: PublicApiWorkflowRowInput["published"],
): PublicApiTeamOption[] {
  if (!published) return [];
  return published.steps
    .filter((step) => step.target_type === "team")
    .map((step) => ({
      id: step.target_config_id,
      name: step.target_name || step.name,
      slug: step.target_slug || step.target_config_id,
      stepName: step.name,
    }));
}

function publishedTeamsAsOptions(
  teams: PublicApiPublishedTeamInput[],
): PublicApiTeamOption[] {
  return teams.map((team) => ({
    id: team.id,
    name: team.name,
    slug: team.slug,
    stepName: team.name,
  }));
}

/**
 * Build the Public API try-it catalog from `/admin/workflows` (+ optional published teams).
 * Lists every published workflow. Team options prefer workflow team steps; when a
 * workflow has none, fall back to all published teams so the dropdown is not blank.
 */
export function buildPublicApiRunCatalog(
  rows: PublicApiWorkflowRowInput[],
  publishedTeams: PublicApiPublishedTeamInput[] = [],
): PublicApiCatalogLoad {
  const published = rows.filter((row) => row.published != null);
  const catalog: PublicApiCatalogRow[] = [];

  for (const row of published) {
    const publishedVersion = row.published!;
    const stepTeams = teamStepsFromPublished(publishedVersion);
    const teamsFromWorkflowSteps = stepTeams.length > 0;
    const teams = teamsFromWorkflowSteps
      ? stepTeams
      : publishedTeamsAsOptions(publishedTeams);
    const summary: WorkflowSummary = {
      id: row.id,
      name: row.name,
      slug: row.slug,
      mode: publishedVersion.mode,
      status: "published",
      stepCount: publishedVersion.steps.length,
      publishedVersion: publishedVersion.version,
      updatedAt: row.updated_at,
    };
    catalog.push({ workflow: summary, teams, teamsFromWorkflowSteps });
  }

  return {
    rows: catalog,
    publishedCount: published.length,
    publishedTeamCount: publishedTeams.length,
  };
}

export function publicApiCatalogEmptyHint(load: PublicApiCatalogLoad): string {
  if (load.rows.length > 0) return "";
  return "No published workflows yet. Publish a workflow first.";
}

export function publicApiTeamEmptyLabel(row: PublicApiCatalogRow | undefined): string {
  if (!row) return "No published teams";
  if (row.teams.length > 0) return "";
  return "No published teams — publish a team first";
}

export function publicApiWorkflowTeamHint(
  row: PublicApiCatalogRow | undefined,
): string {
  if (!row || row.teamsFromWorkflowSteps) return "";
  if (row.teams.length === 0) {
    return "This workflow has no team steps, and there are no published teams yet.";
  }
  return (
    "This workflow has no team steps. Public runs require a team that is a " +
    "step on the published workflow — selecting a published team below will " +
    "fail until you add it as a step and republish."
  );
}

/** Map common public-run HTTP errors into clearer Try-it copy. */
export function formatPublicApiRunError(message: string): string {
  if (/Team is not a step in this published workflow/i.test(message)) {
    return (
      "That team is not a step on the selected published workflow. " +
      "Add the team as a workflow step and republish, then try again."
    );
  }
  if (/Published workflow not found/i.test(message)) {
    return "Published workflow not found. Publish the workflow and try again.";
  }
  if (/Published team not found/i.test(message)) {
    return "Published team not found. Publish the team and try again.";
  }
  return message;
}
