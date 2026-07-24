# Architecture

## Runtime topology

```text
Browser (Next.js)
  ├─ Admin UI ──JWT──▶ AgentOS /admin/* + /v1/agents/tenant-agent/runs
  └─ Customer chat / embed ──guest──▶ AgentOS /public/t/{slug}/…/runs
                │
                ▼
        Single AgentOS (FastAPI)
                │
                ▼
     Shared Postgres + pgvector
     (app tables + AgentOS storage)
```

## Tenant isolation

1. Clerk org claim → application `tenant_id` (admin / signed-in paths)
2. Public chat resolves `tenant_id` from the URL slug only, then sets RLS
3. Repository predicates require `tenant_id`
4. Postgres RLS via `set_config('app.tenant_id', …, true)`
5. Agent factory resolves config/version/tools/knowledge only for that tenant

## Draft vs publish

- Admins edit drafts and preview with `preview=true`
- Publish marks a version immutable and sets `agent_configs.published_version_id`
- Customer sessions pin the published `agent_version_id`
- Public chat and embeds never run drafts

## Public customer chat and embeds

- Hosted: `/t/{tenantSlug}/chat/{agentSlug}` (also teams/workflows routes)
- Embed: `/embed/{tenantSlug}/agent/{agentSlug}` (iframe-friendly); copy snippets from the agent/team/workflow editor **Share / Embed** panel after publishing
- Runs: `POST /public/t/{tenantSlug}/agents/{agentSlug}/runs` with `X-Guest-Id` (no Clerk org membership). Rate-limited per guest and IP.
- Widget snippets contain only public URLs — never PATs, JWTs, or admin credentials

## Self-serve onboarding

- Signed-in Clerk org admins whose org is not provisioned can create a workspace at `/admin/onboarding`
- `clerk_org_id` is taken from the verified JWT, never from the request body

## Isolation trade-offs

Shared runtime is the default for cost and operability. Dedicated DB/runtime cells remain possible later behind the same factory/credential interfaces.
