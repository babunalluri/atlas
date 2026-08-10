# AWS deployment target

Recommended first production shape:

| Concern | Service |
| --- | --- |
| Backend / AgentOS | ECS Fargate service behind ALB |
| Frontend | ECS Fargate or Amplify/CloudFront |
| Database | RDS PostgreSQL with pgvector |
| Documents | S3 + private signed URLs |
| Secrets | Secrets Manager + KMS envelope keys |
| Auth | **Keycloak** (OIDC) verified via JWKS — see [`auth.md`](./auth.md) |
| Quotas | API Gateway / WAF + Redis (ElastiCache) |
| Sandboxed Python | ECS RunTask (Fargate) + deny-all SG |

Keep `AUTH_DISABLED=false` in every non-local environment. Set
`ENVIRONMENT=production` (or `staging`) so production guards apply — default
`development` is for local compose only. Admin API rate limits run whenever
`RATE_LIMITS_ENABLED=true` (the default), independent of `ENVIRONMENT`. Rotate
`CREDENTIAL_ENCRYPTION_KEY` / KMS CMK on a schedule and rehearse restore from RDS
snapshots.

## Sandboxed Python tools

Local development uses the `sandbox-manager` sidecar (Docker socket owner). In
AWS, do **not** put the Docker socket on the backend task. Instead:

1. Build and push `atlas-sandbox-python` to ECR whenever
   `platform_python_packages` / `requirements-allowlist.txt` changes.
2. Configure the backend with:
   - `SANDBOX_MANAGER_URL` pointing at an internal RunTask worker **or**
   - replace the local manager client with an ECS `RunTask` client that starts
     the guest task with `networkMode=awsvpc` and a **deny-all** security group
     (no egress).
3. Mediate HTTP the same way as local: guest IPC → host orchestrator →
   `SafeRestClient` allowlist. Keep the proxy callback on an internal-only
   listener (`SANDBOX_CALLBACK_BASE_URL`).
4. Cap task CPU/memory (0.5 vCPU / 512MB), wall-clock timeout (~30s), and
   per-tenant concurrency. Stop/kill the task after `RunResult` or timeout.
5. Never mount application secrets, the RDS network path, or `docker.sock` into
   the guest task.

The backend continues to own credential decryption and host allowlisting; guest
code must not receive raw tenant secrets in environment variables when they can
be injected on proxied requests instead.

