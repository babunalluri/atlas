# Multi-Tenant Agent SaaS

A branded platform for configuring and operating tenant-isolated Agno agents. The runtime uses AgentOS over FastAPI, Postgres/pgvector for durable state, **Keycloak (OIDC)** for staff identity, and Next.js for the admin and customer experiences.

## Architecture

- One AgentOS service uses tenant-aware factories to build a fresh Agno `Agent` / `Team` from verified claims on each run.
- Every customer-owned application row has a non-null `tenant_id`. Repository filters and PostgreSQL row-level security provide independent isolation layers.
- Draft configurations are available only to tenant/platform admins. Publishing creates an immutable version; conversations pin that version.
- Credentials support platform defaults and tenant BYOK. Encrypted values never reach the browser or agent prompt.
- Mutating tools pause for approval and can be resolved only by tenant/platform admins.
- Atlas schedules pin published target versions and execute through the same tenant-aware factories as interactive runs.

See [docs/architecture.md](docs/architecture.md), [docs/auth.md](docs/auth.md), [docs/aws-deployment.md](docs/aws-deployment.md), [docs/oci-deployment.md](docs/oci-deployment.md), and [docs/oci-free-tier-pilot.md](docs/oci-free-tier-pilot.md) (non-prod $0 OCI pilot).

## Repository layout

```text
apps/backend   Python AgentOS API, factories, RLS, admin routes
apps/web       Next.js admin + branded customer chat
packages/contracts  Shared / generated OpenAPI types
docker-compose.yml  Postgres (pgvector) + backend + web
```

## Local setup

1. Copy the environment template:
   ```sh
   cp .env.example .env
   ```
2. Default local auth is **Keycloak OIDC** (`AUTH_DISABLED=false`, `NEXT_PUBLIC_DEV_AUTH=false`). Start Keycloak with Compose (port 8080). Put secrets in `.env` / `apps/web/.env.local`. For a no-IdP bypass only, set both auth flags to `true` (never in shared environments).
3. Start the stack:
   ```sh
   docker compose up --build
   ```
4. Open:
   - App: http://localhost:3000
   - Keycloak: http://localhost:8080 (admin / admin)
   - Admin agents: http://localhost:3000/admin/agents
   - Admin schedules: http://localhost:3000/admin/schedules
   - Demo chat: http://localhost:3000/t/acme/chat/support
   - API docs: http://localhost:7777/docs

Seed tenants: `acme` and `globex`. Dev IdP users: `admin@atlas.local` / `atlas-admin`, `ops@acme.atlas.local` / `atlas-acme`.

## Useful commands

```sh
# Backend unit/integration tests
cd apps/backend && python -m pip install -e ".[dev]" && pytest -q

# Frontend tests + typecheck
npm run test:web
npm run typecheck:web

# Refresh OpenAPI artifact used by packages/contracts
npm run generate:contracts
```

## Custom Python tools

Platform developers can add reviewed Python API integrations under
`apps/backend/src/app/tools/custom/`. Definitions select only an explicit
registry key; arbitrary source, import paths, packages, and commands are never
accepted from tenants. See [docs/custom-python-tools.md](docs/custom-python-tools.md)
for the implementation template and security requirements.

## Staff authentication (Keycloak)

See [docs/auth.md](docs/auth.md). Atlas verifies OIDC JWTs via JWKS (`AUTH_ISSUER` /
`AUTH_JWKS_URL`). Web sign-in uses Auth.js + Keycloak — **Clerk is not required**.

IdP access-token claims should include:

```json
{
  "org_id": "org_demo_acme",
  "org_role": "org:admin",
  "scopes": ["agents:read", "agents:run", "sessions:read"],
  "platform_admin": true
}
```

Do not accept tenant, role, scopes, or user identity from request bodies. Set
`platform_admin=true` on the IdP user for internal operators, then sign out and
back in so the access token refreshes. That user will see **Platform → Super
admin**, where they can provision or suspend tenants and open a tenant
workspace. Workspace selection is sent as a dedicated header; the backend
accepts it only after verifying the platform-admin JWT claim, then continues to
use the selected tenant's normal RLS context.

## Scheduler

Agno 2.7.4's native schedule records and routes are global and its HTTP executor forwards only an internal bearer token, so `AgentOS(scheduler=True)` is intentionally not enabled in this shared-runtime deployment. Atlas owns `schedules` and `schedule_runs`, both protected by tenant RLS and composite foreign keys.

An in-process async poller enumerates active tenants, opens one RLS-scoped transaction per tenant, atomically advances due schedules, and executes pinned published targets through the existing factories. Configure it with `SCHEDULER_ENABLED` and `SCHEDULER_POLL_SECONDS` (default 15). For horizontally scaled production, run the poller in one designated backend process or extract the same worker loop into a dedicated service; the compare-and-update claim prevents duplicate claims in normal multi-process races but this is not a distributed queue.

## Isolation model

**Chosen default:** shared Postgres with `tenant_id` + RLS, one AgentOS deployment.

### Atlas MCP server

Tenant administrators can enable Atlas's outbound MCP surface at
`/admin/mcp`; compatible clients connect to `POST /mcp` with a staff OIDC token or,
preferably, a scoped Atlas service-account token. Machine clients need
`mcp:access` to connect plus `mcp:read`, `mcp:run`, and/or
`mcp:sessions:read` for the operations they use. The tenant ID is always
derived from verified claims and is not accepted in MCP tool arguments.

This is intentionally an Atlas-owned MCP gateway rather than Agno 2.7.4's
native `AgentOS(mcp_server=True)` surface. The native surface uses the
process-wide AgentOS registry and generic storage operations, which cannot be
proven tenant-safe in this shared-runtime deployment without a substantial
fork. Atlas exposes only tenant-filtered discovery, published resource runs,
and accessible session metadata. Run cancellation is not exposed because the
current runtime has no tenant-safe cancellation primitive; paused runs continue
through the existing approval API. Normal API rate and concurrency limits
apply. MCP client definitions under **Tools** are separate inbound connections
and are unaffected by this server setting.

| Approach | Pros | Cons |
| --- | --- | --- |
| Shared DB + RLS (this repo) | Cheap, simple ops, dynamic factories | Larger blast radius for bugs / noisy neighbors |
| Schema/DB per tenant | Stronger storage isolation | Heavier migrations and routing |
| Runtime per tenant | Strongest compute isolation | Highest cost and operational complexity |

## Security boundaries

This scaffold includes tenant filters, RLS policies, SSRF controls, bounded uploads, approval RBAC, and trace redaction. Before production, add malware scanning, distributed rate/concurrency enforcement, object retention jobs, key rotation, backup/restore exercises, and independent penetration testing. Raw chain-of-thought is intentionally neither stored nor exposed.
