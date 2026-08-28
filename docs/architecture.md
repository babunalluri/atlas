# Architecture

## Runtime topology

```text
Browser (Next.js)
  ├─ Admin UI ──JWT──▶ AgentOS /admin/* + /v1/agents/tenant-agent/runs
  │                    + /interfaces/agui|a2a (JWT, factory-backed)
  └─ Customer chat / embed ──guest──▶ AgentOS /public/t/{slug}/…/runs
                │
Customer email ─┼──Resend webhook──▶ /public/webhooks/resend
Slack/Telegram/WhatsApp ──webhooks──▶ /public/webhooks/{provider}?tenant=…
                │
                ▼
        Single AgentOS (FastAPI)
                │
                ▼
     Shared Postgres + pgvector
     (app tables + AgentOS storage)
```

Native AgentOS `mcp_server` / scheduler and unscoped control-plane routes stay
disabled; Atlas owns schedules, approvals, sessions, and channel interfaces.

## Tenant isolation

1. Clerk org claim → application `tenant_id` (admin / signed-in paths)
2. Public chat resolves `tenant_id` from the URL slug only, then sets RLS
3. Public email resolves `tenant_id` from the inbound address local-part, then sets RLS
4. Channel webhooks resolve `tenant_id` from the `tenant` query slug, then match a `channel_bindings` row
5. Repository predicates require `tenant_id`
6. Postgres RLS via `set_config('app.tenant_id', …, true)` — runtime DB role must be
   `NOSUPERUSER` / `NOBYPASSRLS` (compose uses `agent_saas_app`; migrations use owner URL)
7. Agent factory resolves config/version/tools/knowledge only for that tenant

## Draft vs publish

- Admins edit drafts and preview with `preview=true`
- Publish marks a version immutable and sets the config’s `published_version_id`
- Public customer sessions pin published **team** or **workflow** versions
- Agents are composed into teams/workflows; they are not top-level public targets
  (except JWT AG-UI/A2A under `/interfaces/…/{agent|team}/{slug}`)
- Public chat, embeds, email, and messaging channels never run drafts

## Public customer chat and embeds

- Hosted: `/t/{tenantSlug}/teams/{teamSlug}` or `/t/{tenantSlug}/workflows/{workflowSlug}`
- Embed: `/embed/{tenantSlug}/team|workflow/{slug}` (iframe-friendly); copy snippets from **Share / Embed** after publishing a team or workflow
- Runs: `POST /public/t/{tenantSlug}/teams|workflows/{slug}/runs` with `X-Guest-Id` (no Clerk org membership). Rate-limited per guest and IP.
- Widget snippets contain only public URLs — never PATs, JWTs, or admin credentials

## Verified end-user identity (any org)

- Public customers can bind a chat session to an email via OTP (`/public/t/{slug}/identity/challenge|verify|status`)
- Inbound email claims identity from the From address (mailbox control)
- Bound identity is injected into public runs; tools `my_profile` / `update_my_profile` use the session bind only (never model-supplied ids)
- Staff manage verified customers at `/admin/customers` (enable/disable)

## Public email channel (Resend)

- Inbound addresses: `team-{tenantSlug}.{teamSlug}@{EMAIL_INBOUND_DOMAIN}` or `workflow-…`
- Webhook: `POST /public/webhooks/resend` (Svix-signed). Configure the route in Resend to this URL.
- Atlas runs the published team/workflow (non-streaming), then replies via the tenant `resend` credential (platform `RESEND_API_KEY` fallback).
- Share / Embed shows the copyable email address when `EMAIL_INBOUND_DOMAIN` is set.
- HITL pauses send an auto-reply pointing staff to `/admin/approvals`.

## Messaging channel bindings

- Admin: `/admin/channels` (+ Integrations UI) stores `channel_bindings` (provider, credential, team|workflow target, `external_config`)
- Public: `POST /public/webhooks/slack|telegram|whatsapp?tenant={slug}&binding_id=…`
- Namespaced session ids (`slk_…` / `tel_…` / `wha_…`) mirror the email channel pattern

## Knowledge connectors

- Beside upload: `POST /admin/knowledge/bases/{id}/ingest/url|s3|github` (kinds `url` / `s3` / `github`), all reusing the same chunk/embed indexer

## Framework adapters

- Agent draft `framework_adapter`: `agno` (default) | `langgraph` | `dspy` | `claude_agent_sdk` | `antigravity` (stored on version `team_config`)
- Factory branches to Agno adapter classes when non-`agno`; missing deps or required adapter config → HTTP 400

## Self-serve onboarding

- Signed-in org admins whose org is not provisioned can create a workspace at `/admin/onboarding`
- Organization id is taken from the verified JWT, never from the request body

## Staff user invites

- Atlas tenant ↔ identity organization (`tenants.auth_org_id`)
- Atlas user ↔ identity user: create/invite under `/admin/users` provisions a
  **pending** membership until first sign-in binds by email
- Role changes sync org role (`org:admin` / `org:member`); delete removes org membership
- Keycloak (or your IdP) handles user creation and org group assignment

## Isolation trade-offs

Shared runtime is the default for cost and operability. Dedicated DB/runtime cells remain possible later behind the same factory/credential interfaces.

## Trading desk

Stock-broker desk surfaces (Signal Engine, Options Lab, Param Chart) share instrument identity via Postgres `desk_instrument` and optional same-tab `sessionStorage` handoff. Signal matrix live state lives in Redis; Lab chain cache is separate.

- [Desk instrument board](desk-instrument.md) — architecture, Redis/Postgres keys, Signal → Lab walkthrough.
- [Options Lab market profile](options-lab-market-profile.md) — deferred US/IN market abstraction.
