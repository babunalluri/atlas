# Editable sandboxed Python tools

Tenant admins can author Python tools in the UI (`tenant_python`). Source is
validated, versioned, and executed only inside ephemeral isolated containers.
This is separate from source-controlled [`custom_python`](./custom-python-tools.md).

## Lifecycle

1. Create a tool with kind **Editable Python** in Tool builder.
2. Edit source in the Monaco editor (optional starter templates may appear in
   the UI — they are conveniences only). Capability names are discovered from
   top-level `async def`s or public methods on a `BaseToolkit`/`Toolkit`
   subclass — no manual capability list. Optional allowlisted dependencies can
   still be selected.
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

## Outbound HTTPS

Before a sandboxed tool can call an external HTTPS API, add that API’s **exact
hostname** to `REST_TOOL_ALLOWED_HOSTS` / `BACKEND_ALLOWED_OUTBOUND_HOSTS`.
Matching is exact (no wildcard suffix). Keep platform env examples generic
(e.g. `api.example.com`); add real hosts only in the environments that need
them. Project-specific hosts belong with that project’s tool instructions, not
in platform defaults.

Typical setup:

1. Allowlist the hostname from the tool’s `base_url`.
2. Create a tenant credential (encrypted) — Bearer token and/or JSON settings
   merged at runtime, depending on how the tool authenticates.
3. Set tool settings (`base_url`, timeouts, non-secret options).
4. Bind credential → **Validate** → **Publish** → attach to an agent.

## Isolation model

```
Agent tool call
  → TenantPythonProvider
  → SandboxOrchestrator (backend)
  → sandbox-manager (HTTP, owns docker.sock)
  → atlas-sandbox-python container (--network none)
       ↕ JSON-RPC stdin/stdout
  → HttpProxy callback on backend
  → SafeRestClient (HTTPS host allowlist; JSON or form-urlencoded bodies)
```

- Backend never mounts `docker.sock`.
- Guest has no network; all HTTPS is host-mediated.
- Credentials are injected on proxied requests on the host when possible
  (e.g. Bearer), or merged into sandbox settings when the tool expects tokens
  in the request body / settings object.
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

## Oracle Cloud / OKE path

See [oci-deployment.md](./oci-deployment.md#sandboxed-python-tools).
