import { z } from "zod";

import {
  ALLOWED_MODELS,
  TOOL_CATALOG,
  type ModelId,
  type ToolKind,
} from "@/lib/api/types";

const modelIds = ALLOWED_MODELS.map((m) => m.id) as [ModelId, ...ModelId[]];
const toolKinds = TOOL_CATALOG.map((t) => t.kind) as [ToolKind, ...ToolKind[]];

export const toolBindingSchema = z.object({
  id: z.string().min(1),
  kind: z.enum(toolKinds),
  label: z.string().min(1),
  enabled: z.boolean(),
  config: z.record(z.unknown()),
  requiresApproval: z.boolean(),
  // Tenant reusable tools; must survive Save validation or backendDraft
  // falls back to tool_key=kind (often rest_mutate) and drops the definition.
  definitionId: z.string().min(1).optional(),
});

export const agentDraftSchema = z.object({
  name: z
    .string()
    .trim()
    .min(2, "Name must be at least 2 characters")
    .max(80, "Name is too long"),
  slug: z
    .string()
    .trim()
    .regex(
      /^[a-z0-9]+(?:-[a-z0-9]+)*$/,
      "Slug must be lowercase letters, numbers, and hyphens",
    )
    .min(2)
    .max(64),
  description: z.string().trim().max(280).default(""),
  instructions: z
    .string()
    .trim()
    .min(16, "Instructions need more detail for a reliable agent")
    .max(20_000),
  model: z.enum(modelIds),
  temperature: z.number().min(0).max(1.5),
  memoryMode: z.enum(["session", "persistent"]),
  tools: z.array(toolBindingSchema),
  knowledgeBaseId: z.string().nullable(),
  frameworkAdapter: z.enum([
    "agno",
    "langgraph",
    "dspy",
    "claude_agent_sdk",
    "antigravity",
  ]),
  guardrails: z.object({
    promptInjection: z.boolean(),
    piiDetection: z.boolean(),
    openaiModeration: z.boolean(),
  }),
});

export type AgentDraftFormValues = z.infer<typeof agentDraftSchema>;

export function slugifyName(name: string): string {
  return name
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 64);
}

/** Short unique slug for Create flows — avoid embedding Date.now() digits in the UI. */
export function provisionalSlug(prefix: string): string {
  const base = slugifyName(prefix) || "item";
  const suffix = Math.random().toString(36).slice(2, 8);
  return `${base}-${suffix}`.slice(0, 64);
}

/** True when slug looks auto-generated (untitled + digits / random junk). */
export function isProvisionalSlug(slug: string): boolean {
  return /^(untitled|untitled-team|untitled-workflow|untitled-agent|team|agent|workflow)(-[a-z0-9]+)+$/i.test(
    slug,
  );
}

export function validateAgentDraft(input: unknown) {
  return agentDraftSchema.safeParse(input);
}
