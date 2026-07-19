# Architecture

## Runtime topology

```text
Browser (Next.js)
  ├─ Admin UI ──JWT──▶ AgentOS /admin/* + /v1/agents/tenant-agent/runs
  └─ Customer chat ──JWT──▶ AgentOS streaming runs
                │
                ▼
        Single AgentOS (FastAPI)
                │
                ▼
     Shared Postgres + pgvector
     (app tables + AgentOS storage)
```

## Tenant isolation

1. Clerk org claim → application `tenant_id`
2. Repository predicates require `tenant_id`
3. Postgres RLS via `set_config('app.tenant_id', …, true)`
4. Agent factory resolves config/version/tools/knowledge only for that tenant

## Draft vs publish

- Admins edit drafts and preview with `preview=true`
- Publish marks a version immutable and sets `agent_configs.published_version_id`
- Customer sessions pin the published `agent_version_id`

## Isolation trade-offs

Shared runtime is the default for cost and operability. Dedicated DB/runtime cells remain possible later behind the same factory/credential interfaces.
