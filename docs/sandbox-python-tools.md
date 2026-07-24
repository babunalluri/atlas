# Editable sandboxed Python tools

Tenant admins can author Python tools in the UI (`tenant_python`). Source is
validated, versioned, and executed only inside ephemeral isolated containers.
This is separate from source-controlled [`custom_python`](./custom-python-tools.md).

## Lifecycle

1. Create a tool with kind **Editable Python** in Tool builder.
2. Edit source (or load the **Contact Center PBX starter**). Declare capabilities
   and optional allowlisted dependencies.
3. **Save** writes a draft `tool_definition_versions` row and keeps
   `config.version_status=draft`.
4. **Validate** runs AST checks and dependency allowlist checks; marks the draft
   `validated`.
5. **Publish** pins `tool_definitions.published_version_id` and sets
   `version_status=published`. Only published tools can be attached/run by agents.
6. Mutating capabilities always require HITL approval.

## CC PBX example (Contact Center PBX starter)

The built-in `cc_pbx` template wraps CloudConnect / HODU PBX REST APIs
(`hodupbx_api/v1.4` and `ccpl_api/v1.4`). It exposes 11 capabilities:

| Category | Capabilities | Mutating |
|----------|--------------|----------|
| Provisioning | `create_tenant`, `create_extension`, `create_did`, `add_balance` | yes |
| Reference | `get_billplan`, `get_rateplan`, `get_og_rule`, `get_did_details` | no |
| Monitoring | `get_balance`, `get_active_calls`, `get_call_log` | no |

### Setup

1. **Allowlist the API host** — add `dev2.cloud-connect.in` to
   `BACKEND_ALLOWED_OUTBOUND_HOSTS` (or `REST_TOOL_ALLOWED_HOSTS`) in your
   backend `.env` for local dev. Never commit live tokens.
2. **Create a tenant credential** with JSON value (stored encrypted):

   ```json
   {
     "pbx_token_id": "<hodupbx_api token>",
     "ccpl_token_id": "<ccpl_api token>",
     "ccpl_unique_token": "<per-tenant unique token for call logs>"
   }
   ```

   CC PBX auth sends `token_id` in each POST body — not an `Authorization`
   header. The provider merges this JSON into sandbox settings at runtime and
   skips Bearer injection when body-token keys are present.
3. **Load the CC PBX starter** in Tool builder. Default settings:

   ```json
   {
     "base_url": "https://dev2.cloud-connect.in",
     "pbx_api_root": "hodupbx_api/v1.4/api",
     "ccpl_api_root": "ccpl_api/v1.4/api",
     "timeout": 60
   }
   ```

4. Bind the credential to the tool, **Validate**, then **Publish** before
   attaching it to an agent.

Use reference lookups (`get_billplan`, `get_og_rule`, etc.) before provisioning
calls to resolve IDs for `create_tenant` and `create_did`.

## Isolation model

```
Agent tool call
  → TenantPythonProvider
  → SandboxOrchestrator (backend)
  → sandbox-manager (HTTP, owns docker.sock)
  → atlas-sandbox-python container (--network none)
       ↕ JSON-RPC stdin/stdout
  → HttpProxy callback on backend
  → SafeRestClient (HTTPS host allowlist)
```

- Backend never mounts `docker.sock`.
- Guest has no network; all HTTPS is host-mediated.
- Credentials are injected on proxied requests on the host, not into guest env
  when possible. CC PBX-style tools merge credential JSON into sandbox settings
  (body tokens) instead of Bearer headers.
- Limits: ~30s wall clock, 0.5 vCPU, 512MB, capped result size, per-tenant
  concurrency.

## Local compose

```bash
# Build the guest image once (or after allowlist changes)
docker compose --profile build-sandbox build sandbox-python
# or: docker build -t atlas-sandbox-python:local ./services/sandbox-python

docker compose up -d sandbox-manager backend
```

Backend env:

- `SANDBOX_MANAGER_URL=http://sandbox-manager:8090`
- `SANDBOX_CALLBACK_BASE_URL=http://backend:7777`
- `SANDBOX_PYTHON_IMAGE=atlas-sandbox-python:local`

## Platform package allowlist

Platform admins manage pins at `/admin/platform/sandbox-packages` (API:
`/admin/platform/sandbox-packages`). Tenant editors can only select **active**
packages. After changing the allowlist, rebuild `atlas-sandbox-python` so the
image contains the new wheels/hashes.

## AWS / ECS Fargate path

See [aws-deployment.md](./aws-deployment.md#sandboxed-python-tools).
