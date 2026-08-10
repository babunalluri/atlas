# Atlas authentication

Atlas staff identity is any **OIDC** provider that issues JWTs Atlas can verify via
JWKS. Claims stay stable so tenant RBAC does not change.

## Chosen architecture

| Layer | Choice | Why |
| --- | --- | --- |
| **Identity (IdP)** | **Keycloak** (Apache 2.0, self-hosted) | Free forever on OCI/local; orgs/groups; OIDC; Admin UI for users/invites |
| **Web session** | **Auth.js** (`next-auth`) Keycloak provider | Open-source session layer; no SaaS fee |
| **API trust** | FastAPI JWKS verify | RS256 + issuer/audience — point at Keycloak |
| **Edge / API gateway** | **Optional Traefik or Apache APISIX** | TLS, routing, rate limits only — **not** a user database |
| **Customer chat OTP / PATs** | Unchanged | Never went through staff IdP |

Do **not** use an API gateway alone as an identity provider: Kong/Traefik/APISIX
do not give you signup, orgs, or invites. Put Keycloak (or Zitadel) behind the
gateway if you want one public entrypoint.

```text
Browser → (optional Traefik) → Next.js (Auth.js)
                ↓ OIDC login
            Keycloak
                ↓ access JWT (org_id, org_role, platform_admin, …)
            Atlas API (JWKS verify) → tenants.auth_org_id / memberships
```

`tenants.auth_org_id` is the **external org id** string (Keycloak group/org id
such as `org_demo_acme`).

## Local Compose

```bash
docker compose up -d keycloak postgres redis backend web
# Keycloak admin: http://localhost:8080  (admin / admin)
# Realm: atlas  · Client: atlas-web
```

Default seed users (dev only — change in prod):

| User | Password | Org claim |
| --- | --- | --- |
| `admin@atlas.local` | `atlas-admin` | platform admin |
| `ops@acme.atlas.local` | `atlas-acme` | `org_demo_acme` |

## Environment

```bash
AUTH_PROVIDER=oidc
AUTH_ISSUER=http://localhost:8080/realms/atlas
AUTH_JWKS_URL=http://localhost:8080/realms/atlas/protocol/openid-connect/certs
AUTH_AUDIENCE=atlas-web
# Web (Auth.js)
AUTH_URL=http://localhost:3000
AUTH_SECRET=<random 32+ chars>
AUTH_KEYCLOAK_ID=atlas-web
AUTH_KEYCLOAK_SECRET=<from Keycloak client>
AUTH_KEYCLOAK_ISSUER=http://localhost:8080/realms/atlas
NEXT_PUBLIC_AUTH_PROVIDER=keycloak
```

Set `AUTH_DISABLED=false` and `NEXT_PUBLIC_DEV_AUTH=false` for real OIDC locally.

Access tokens are short-lived; Auth.js refreshes them via the Keycloak refresh
token before calling the Atlas API. Access tokens must include `org_id`,
`org_role`, `platform_admin` (optional), `email` (for invite binding), and
audience `atlas-web` (realm client mappers in `infra/keycloak/atlas-realm.json`).

If you already imported the realm before mapper updates, either delete the
`keycloak_data` volume and re-import, or add the mappers manually in the Admin UI.

## Invites

1. Create the user (or send Keycloak execute-actions email) in Keycloak Admin.
2. Put them in the org group matching `tenants.auth_org_id`.
3. Atlas `/admin/users` can still create a **pending** membership row bound on
   first login by email (`pending:…` → real `sub`).

Optional later: Keycloak Admin REST client behind the same `IdentityAdminClient`
interface.

## OCI

Run Keycloak as an OKE Deployment (or the [Keycloak Operator](https://www.keycloak.org/operator/installation)),
Postgres for Keycloak separate from Atlas DB, private NetworkPolicy, public only
via Traefik/LB. See [`oci-deployment.md`](./oci-deployment.md).

## Cost

| | SaaS B2B IdP | Keycloak self-hosted |
| --- | --- | --- |
| License | ~$100+/mo | **$0** |
| Ops | Low | You run the pod + backups |
| Data residency | Vendor SaaS | Your OCI tenancy |

## Alternatives (same Atlas JWT contract)

- **Zitadel** — also strong for multi-tenant OIDC
- **Authentik** — fine IdP, slightly different org model
- Homegrown password auth — possible but higher security risk; not recommended
  as the first cut when Keycloak is free
