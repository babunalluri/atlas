# Editable sandboxed Python tools

Tenant admins can author Python tools in the UI (`tenant_python`). Source is
validated, versioned, and executed only inside ephemeral isolated containers.
This is separate from source-controlled [`custom_python`](./custom-python-tools.md).

## Lifecycle

1. Create a tool with kind **Editable Python** in Tool builder.
2. Edit source (or load the CC PBX starter). Declare capabilities and optional
   allowlisted dependencies.
3. **Save** writes a draft `tool_definition_versions` row and keeps
   `config.version_status=draft`.
4. **Validate** runs AST checks and dependency allowlist checks; marks the draft
   `validated`.
5. **Publish** pins `tool_definitions.published_version_id` and sets
   `version_status=published`. Only published tools can be attached/run by agents.
6. Mutating capabilities always require HITL approval.

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
  when possible.
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
