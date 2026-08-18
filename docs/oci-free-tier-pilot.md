# OCI Always Free — pilot deployment

Run Atlas on a **single Oracle Cloud VM at $0/month** for non-production use:
internal demos, Stock Broker desk rehearsal, signal-engine mock mode, and
integration testing. **Not** for live customer trading or HA production — see
[`oci-deployment.md`](./oci-deployment.md) when you are ready to pay for managed
services.

## What you get for $0

| Resource | Always Free allowance | Pilot use |
| --- | --- | --- |
| **Compute** | Ampere A1 — **2 OCPU, 12 GB RAM** (home region) | One VM runs full `docker compose` stack |
| **Block storage** | **200 GB** total | Boot volume + Docker volumes |
| **Object Storage** | **20 GB** (Standard tier in home region) | Optional; MinIO in Compose is fine for pilot |
| **Load Balancer** | 1× Flexible LB @ **10 Mbps** | HTTPS front door (optional — see below) |
| **Egress** | First 10 TB/month free (most regions) | Usually enough for pilot traffic |

**Not included free:** managed PostgreSQL, OCI Cache (Redis), OKE worker pools.
The pilot self-hosts Postgres and Redis on the VM (same as local Compose).

**Extra cost (not OCI):** LLM API usage (OpenAI, Anthropic, etc.) when agents run.

> **Aug 2026 note:** Always Free Ampere is **2 OCPU / 12 GB** total. Size the
> VM to that limit in the OCI Console.

## Architecture (pilot)

```text
Internet
   │
   ▼
[ Always Free LB @ 10 Mbps ]   ← optional TLS termination
   │  or Caddy/nginx on VM :443
   ▼
VM (A1 Flex 2 OCPU / 12 GB)
   docker compose
   ├─ web          :3000
   ├─ backend      :7777
   ├─ keycloak     :8080
   ├─ postgres     (pgvector)
   ├─ redis
   ├─ minio        (documents)
   └─ sandbox-manager (+ sandbox-python image)
```

All services on one host. Acceptable for **pilot only**.

## Prerequisites

- OCI account (Always Free or Pay As You Go with free resources)
- Home region with Ampere capacity — **`ap-mumbai-1`** recommended for India
- Domain name (optional but recommended for Keycloak OIDC redirects)
- SSH key pair

## 1. Create the VM

1. **Compute → Instances → Create**
2. **Shape:** `VM.Standard.A1.Flex` — **2 OCPUs, 12 GB memory**
3. **Image:** Oracle Linux 9 or Ubuntu 22.04/24.04 (aarch64)
4. **Boot volume:** 50–100 GB (within 200 GB free cap)
5. **VCN:** allow inbound **22** (your IP), **80**, **443**; outbound all (or
   restrict later)
6. Assign a **public IPv4** (or use LB only)

Install Docker on the VM:

```bash
# Oracle Linux example
sudo dnf install -y docker-engine docker-compose-plugin git
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"
# log out and back in
```

## 2. Clone and configure

```bash
git clone <your-atlas-repo-url> atlas
cd atlas
cp .env.example .env
```

Edit `.env` for the pilot host. Replace `pilot.example.com` with your domain
or public IP.

```bash
# Pilot — not production, but not local dev either
ENVIRONMENT=staging
LOG_LEVEL=INFO

# Public URLs (must match what browsers hit)
APP_PUBLIC_URL=https://pilot.example.com
NEXT_PUBLIC_APP_URL=https://pilot.example.com
NEXT_PUBLIC_AGENTOS_URL=https://pilot.example.com
BACKEND_URL=https://pilot.example.com
CORS_ORIGINS=https://pilot.example.com

# Strong secrets — generate fresh values; never reuse dev defaults
POSTGRES_PASSWORD=<random>
POSTGRES_APP_PASSWORD=<random>
CREDENTIAL_ENCRYPTION_KEY=<fernet-key>
AUTH_SECRET=<random-32+-chars>
SANDBOX_INTERNAL_TOKEN=<random>
REDIS_PASSWORD=<random>

# Keycloak — browser-facing issuer must match public URL
AUTH_ISSUER=https://auth.pilot.example.com/realms/atlas
AUTH_JWKS_URL=http://keycloak:8080/realms/atlas/protocol/openid-connect/certs
AUTH_AUDIENCE=atlas-web
AUTH_URL=https://pilot.example.com
AUTH_KEYCLOAK_ISSUER=https://auth.pilot.example.com/realms/atlas
NEXT_PUBLIC_AUTH_KEYCLOAK_ISSUER=https://auth.pilot.example.com/realms/atlas

# Redis (in-compose — not OCI Cache)
REDIS_URL=redis://:<REDIS_PASSWORD>@redis:6379/0

# Keep auth on
AUTH_DISABLED=false
NEXT_PUBLIC_DEV_AUTH=false

# Broker tools (Stock Broker pilot)
REST_TOOL_ALLOWED_HOSTS=api.kite.trade,api.groww.in,mcp.groww.in,httpbin.org

# LLM keys as needed
OPENAI_API_KEY=
```

Create `apps/web/.env.local` with the same Auth.js / Keycloak values if you
override web-only settings.

### Keycloak hostname

In `docker-compose.yml`, override Keycloak for production-like hostnames
(via Compose override file or env):

```yaml
# docker-compose.pilot.yml (example override)
services:
  keycloak:
    environment:
      KC_HOSTNAME: https://auth.pilot.example.com
      KC_HOSTNAME_STRICT: "true"
  web:
    environment:
      AUTH_KEYCLOAK_INTERNAL_ISSUER: http://keycloak:8080/realms/atlas
```

Register Keycloak redirect URIs for `https://pilot.example.com/*`.

## 3. Build sandbox image and start

```bash
docker compose --profile build-sandbox build sandbox-python
docker compose -f docker-compose.yml -f docker-compose.pilot.yml up -d --build
```

First boot runs migrations automatically (see `backend` command in Compose).

Check health:

```bash
docker compose ps
curl -sf http://localhost:7777/health
curl -sf -o /dev/null -w '%{http_code}\n' http://localhost:3000
```

## 4. Public access and TLS

**Option A — Always Free Load Balancer (recommended)**

- Backend set: VM port **3000** (web) — Next.js proxies API to backend internally
- Or route `/` → web, `/v1` + `/admin` → backend if you split paths
- Terminate TLS on the LB with your certificate
- Point DNS `pilot.example.com` → LB IP
- Point DNS `auth.pilot.example.com` → same LB with host rule → Keycloak :8080

**Option B — Caddy/nginx on the VM**

- Reverse proxy `:443` → `web:3000` and `auth.*` → `keycloak:8080`
- Use Let's Encrypt (Certbot or Caddy auto-TLS)
- Simpler; no LB quota used

For a quick IP-only smoke test (no TLS): use `http://<public-ip>:3000` and
adjust Keycloak `KC_HOSTNAME` accordingly — OIDC in browsers prefers HTTPS.

## 5. Post-deploy checklist

- [ ] Sign in via Keycloak (`admin@atlas.local` or provisioned user)
- [ ] Open workspace `/t/<tenant>/chat` — Stock Broker desk loads
- [ ] Signal engine: enable **Mock feed**, **Start engine**, metrics update
- [ ] Run one sandbox Python tool (Groww/Kite toolkit smoke test)
- [ ] Set `RATE_LIMITS_ENABLED=true` (default) — verify admin API responds
- [ ] Snapshot the boot volume (manual backup in OCI Console)

## Memory budget (~12 GB)

| Service | Rough RAM |
| --- | --- |
| Postgres | ~1 GB |
| Keycloak | ~1 GB |
| Backend (2 workers) | ~1–1.5 GB |
| Web (Next.js) | ~512 MB–1 GB |
| Redis | ~256 MB |
| MinIO + sandbox-manager | ~512 MB |
| OS + Docker | ~1 GB |

Total ≈ **5–6 GB** — comfortable headroom for pilot. If tight, stop unused
services or reduce `WEB_CONCURRENCY` to `1`.

## Redis on pilot

Use **in-compose Redis** (`REDIS_URL=redis://:password@redis:6379/0`). Atlas
is compatible with OCI Cache when you upgrade — switch `REDIS_URL` to
`rediss://…` later with no code changes. See [`oci-deployment.md`](./oci-deployment.md).

## What to upgrade for production

| Pilot (free) | Production (paid) |
| --- | --- |
| Single VM + Compose | OKE or dedicated compute pool |
| Postgres on VM | OCI Database with PostgreSQL + pgvector |
| Redis on VM | OCI Cache (Redis) |
| MinIO on VM | Object Storage |
| Manual snapshots | Automated backups + restore drills |
| 10 Mbps LB | Higher bandwidth / WAF |
| `ENVIRONMENT=staging` | `ENVIRONMENT=production` + Vault secrets |

Estimated production floor: **~$200+/month** depending on scale — see
[`oci-deployment.md`](./oci-deployment.md).

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Keycloak redirect loop | `AUTH_ISSUER`, `KC_HOSTNAME`, and browser URL must match |
| CORS errors | `CORS_ORIGINS` includes exact web origin |
| Signal engine frozen | Start engine; mock mode still requires engine **Running** |
| Scheduler silent | `REDIS_URL` must be real Redis (not `memory://`) when `ENVIRONMENT=staging` |
| OOM / slow VM | `docker stats`; reduce workers or restart stack |
| Ampere capacity error | Try another AD in home region or retry off-peak |

## Related docs

- [`oci-deployment.md`](./oci-deployment.md) — production OKE / managed services
- [`auth.md`](./auth.md) — Keycloak OIDC setup
- [`architecture.md`](./architecture.md) — platform overview
- [`sandbox-python-tools.md`](./sandbox-python-tools.md) — sandbox security model
