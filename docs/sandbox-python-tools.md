# Editable sandboxed Python tools

Tenant admins can author Python tools in the UI (`tenant_python`). Source is
validated, versioned, and executed only inside ephemeral isolated containers.
This is separate from source-controlled [`custom_python`](./custom-python-tools.md).

## Lifecycle

1. Create a tool with kind **Editable Python** in Tool builder.
2. Edit source in the Monaco editor (or load the **Contact Center PBX starter**).
   Capability names are discovered from top-level `async def`s or public methods
   on a `BaseToolkit`/`Toolkit` subclass — no manual capability list. Optional
   allowlisted dependencies can still be selected.
3. **Save** writes a draft `tool_definition_versions` row and keeps
   `config.version_status=draft`.
4. **Validate** runs AST checks and dependency allowlist checks; marks the draft
   `validated`.
5. **Publish** pins `tool_definitions.published_version_id` and sets
   `version_status=published`. Only published tools can be attached/run by agents.
6. After publish, source is **edit-locked** in the UI until you click **Edit
   source** (creates a new draft). Use **Versions** on the Tools list (before
   Delete) to view / restore draft / restore live published version.
7. Mutating capabilities always require HITL approval.

For local Freshdesk-style tools, add your exact HTTPS host (e.g.
`api.freshdesk.com` or `{domain}.freshdesk.com`) to
`REST_TOOL_ALLOWED_HOSTS` / `BACKEND_ALLOWED_OUTBOUND_HOSTS` — matching is
exact hostname, not a wildcard suffix.

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
- Import AST policy is **deny-list only**: dangerous modules (`os`, `sys`,
  `subprocess`, `socket`, …) and dangerous builtins (`eval`, `exec`, `open`,
  …) are rejected. **Any other import root is allowed at save time**; packages
  missing from the guest image fail at **runtime** with `ImportError`.
- Guest shims on `PYTHONPATH`: `requests` (HttpProxy IPC), `agno.utils.log`,
  `agno.tools.BaseToolkit`, and `supertools.common.base_tool.BaseToolkit`.
  Do not declare real PyPI `requests` as a tool dependency. These are not full
  Agno/supertools frameworks.
- Capabilities may be top-level `async def` functions **or** public methods on
  a `BaseToolkit`/`Toolkit` subclass. The runner instantiates the class (no
  required args), injects settings into `.pv` and `.settings`, and calls the
  method (sync or async).
- Limits: ~30s wall clock, 0.5 vCPU, 512MB, capped result size, per-tenant
  concurrency.

## Local compose

```bash
# Build the guest image once (or after allowlist / shim / SDK changes)
docker compose --profile build-sandbox build sandbox-python
# or: docker build -t atlas-sandbox-python:local ./services/sandbox-python

docker compose up -d --build sandbox-manager backend
```

`sandbox-manager` must ship the Docker CLI (see its Dockerfile) and mount the
host `docker.sock`. Confirm with `GET /v1/diagnose`. Guest `/sandbox/work` is a
tmpfs owned by uid `10001`.

After changing the validator, `atlas_sdk`, or guest shims (`requests`, `agno`,
`supertools`), rebuild `atlas-sandbox-python:local` with the command above.
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
