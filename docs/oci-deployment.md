# Oracle Cloud (OCI) deployment target

Atlas is container-native and can run on Oracle Cloud Infrastructure. This
document mirrors [`aws-deployment.md`](./aws-deployment.md) with OCI service
equivalents. There is no one-click OCI stack in-repo yet — treat this as the
recommended production shape.

Prefer region **Mumbai (`ap-mumbai-1`)** for India-facing workloads (e.g.
Stock Broker tenants); use the home region that matches data-residency needs.

## Recommended first production shape

| Concern | OCI service |
| --- | --- |
| Backend / AgentOS | **OKE** Deployment (or Container Instances) behind a Flexible Load Balancer |
| Frontend (Next.js) | OKE Deployment / Container Instances, or Object Storage + CDN if statically exportable |
| Database | **PostgreSQL** (BaseDB / HeatWave MySQL is **not** a drop-in — use Postgres) with **pgvector** |
| Documents / exports | **Object Storage** + pre-authenticated or signed URLs |
| Container images | **OCIR** (Oracle Cloud Infrastructure Registry) |
| Secrets | **Vault** secrets + encryption keys (envelope encrypt credential blobs) |
| Auth | **Keycloak** (OIDC, self-hosted) verified via JWKS — see [`auth.md`](./auth.md) |
| Quotas / sessions | API Gateway or WAF + **OCI Cache with Redis** (or managed Redis on Compute) |
| Sandboxed Python | OKE **Job** / short-lived Pod with **NetworkPolicy deny-all egress** (no `docker.sock` on API nodes) |
| Observability | OCI Logging + Monitoring (or existing OpenTelemetry → vendor) |

Keep `AUTH_DISABLED=false` in every non-local environment. Set
`ENVIRONMENT=production` (or `staging`) so production guards apply — default
`development` is for local compose only. Admin API rate limits run whenever
`RATE_LIMITS_ENABLED=true` (the default), independent of `ENVIRONMENT`. Rotate
`CREDENTIAL_ENCRYPTION_KEY` / Vault master keys on a schedule and rehearse
restore from Postgres backups.

## Map from local Compose

| Compose service | OCI |
| --- | --- |
| `backend` | OKE Deployment `atlas-backend` |
| `web` | OKE Deployment `atlas-web` |
| `postgres` | Managed Postgres (pgvector extension enabled) or self-managed Postgres on OKE with PVC (managed preferred) |
| `redis` | OCI Cache (Redis) |
| `minio` | Object Storage (S3-compatible API where configured) |
| `sandbox-manager` + `sandbox-python` | See sandboxed Python below — **do not** mount host Docker socket on shared API nodes in production |

Push images to OCIR, for example:

```text
<region>.ocir.io/<tenancy-namespace>/atlas-backend:<tag>
<region>.ocir.io/<tenancy-namespace>/atlas-web:<tag>
<region>.ocir.io/<tenancy-namespace>/atlas-sandbox-python:<tag>
```

## Network sketch

```text
Internet
   │
   ▼
Public LB (TLS)
   ├─ /        → atlas-web
   └─ /api|/v1 → atlas-backend   (or path/host routing as you prefer)
         │
         ├─ private → Postgres (pgvector)
         ├─ private → Redis
         ├─ private → Object Storage (via service gateway / NAT as required)
         └─ internal only → sandbox runner + HttpProxy callback
```

- Backend and DB in **private subnets**.
- Load balancer in a public subnet (or private + Bastion/VPN for Ops-only).
- Egress from backend only to allowlisted SaaS (Clerk JWKS, LLM providers,
  and whatever exact hostnames you put in `REST_TOOL_ALLOWED_HOSTS`).
- Sandbox guest pods: **no egress**; all HTTPS via backend `SafeRestClient`.

## Sandboxed Python tools

Local development uses the `sandbox-manager` sidecar (Docker socket owner). On
OCI, do **not** put the Docker socket on the backend workload. Instead:

1. Build and push `atlas-sandbox-python` to **OCIR** whenever
   `platform_python_packages` / `requirements-allowlist.txt` changes.
2. Configure the backend with:
   - `SANDBOX_MANAGER_URL` pointing at an internal worker that starts guest
     workloads, **or**
   - an OKE client that creates a short-lived Job/Pod from the sandbox image
     with a **deny-all egress** NetworkPolicy (and no access to Postgres/Redis
     Secrets).
3. Mediate HTTP the same way as local: guest IPC → host orchestrator →
   `SafeRestClient` allowlist (JSON and form-urlencoded). Keep the proxy
   callback on an internal-only listener (`SANDBOX_CALLBACK_BASE_URL`).
4. Cap guest CPU/memory (≈0.5 vCPU / 512MB), wall-clock timeout (~30s), and
   per-tenant concurrency. Delete the Job/Pod after `RunResult` or timeout.
5. Never mount application secrets, the database network path, or `docker.sock`
   into the guest.

The backend continues to own credential decryption and host allowlisting; guest
code must not receive raw tenant secrets in environment variables when they can
be injected on proxied requests instead.

See also [`sandbox-python-tools.md`](./sandbox-python-tools.md).

## Required configuration (high level)

Align with `.env.example`, sourced from Vault in production:

- `DATABASE_URL` / `AGNO_DATABASE_URL` / `MIGRATION_DATABASE_URL` → private Postgres
- `REDIS_URL` → OCI Cache
- `CREDENTIAL_ENCRYPTION_KEY` → Vault-managed
- `AUTH_*` (issuer, JWKS URL, audience) → Keycloak or your OIDC IdP
- `REST_TOOL_ALLOWED_HOSTS` / `BACKEND_ALLOWED_OUTBOUND_HOSTS` → exact API hosts
- `SANDBOX_*` → internal manager URL, callback base, OCIR image reference
- `ENVIRONMENT=production`, `AUTH_DISABLED=false`

## Rollout checklist

1. OCIR repos + CI build/push for backend, web, sandbox-python.
2. VCN: public LB subnet + private app/data subnets + service gateway.
3. Postgres with pgvector; run Atlas migrations from a job with DB credentials.
4. Redis + Object Storage buckets (private).
5. OKE Deployments + HPA for backend/web; PDB for backend.
6. Sandbox Job path + NetworkPolicy deny-all egress; smoke-test a `tenant_python` tool.
7. Clerk production instance; WAF / rate limits on public LB.
8. Backup/restore drill for Postgres; secret rotation drill for Vault keys.
9. Status / on-call alerts on 5xx, DB connections, sandbox failures, queue lag.

## AWS ↔ OCI quick map

| AWS (see aws-deployment.md) | OCI |
| --- | --- |
| ECS Fargate | OKE / Container Instances |
| ALB | Flexible Load Balancer |
| RDS PostgreSQL | OCI PostgreSQL (pgvector) |
| S3 | Object Storage |
| ECR | OCIR |
| Secrets Manager + KMS | Vault |
| ElastiCache | OCI Cache (Redis) |
| ECS RunTask + deny-all SG | OKE Job + deny-all NetworkPolicy |

## Out of scope for this doc

- Terraform/Helm charts (add under `infra/` when productized)
- Multi-region active-active
- Replacing Clerk with OCI IAM for end-user login (not required)
