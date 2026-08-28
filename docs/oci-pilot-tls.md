# OCI pilot — enable HTTPS (TLS)

The IP-only desk (`http://137.23.61.107:3000`) works, but browsers treat it as
**non-secure**. Keycloak then logs:

> Non-secure context detected; cookies are not secured

That is expected until the desk runs behind HTTPS with a real hostname.

## Prerequisites

1. A **DNS name** you control (Let's Encrypt does not issue certs for bare IPs).
2. Ports **80** and **443** open on the VM security list / firewall.
3. Two hostnames (recommended):
   - `pilot.example.com` → web (Next.js)
   - `auth.pilot.example.com` → Keycloak

## Steps (Caddy + Compose)

1. Copy the example files onto the VM (operator-owned — never blind rsync over
   live `docker-compose.pilot.yml`):

   ```bash
   cp infra/pilot/Caddyfile.example infra/pilot/Caddyfile
   cp docker-compose.pilot.tls.example.yml docker-compose.pilot.tls.yml
   ```

2. Edit `infra/pilot/Caddyfile` — replace `pilot.example.com` and
   `auth.pilot.example.com` with your domains.

3. Edit `docker-compose.pilot.tls.yml` — set `APP_PUBLIC_URL`, `AUTH_URL`,
   `CORS_ORIGINS`, `KC_HOSTNAME`, and `NEXT_PUBLIC_*` to `https://…` URLs.

4. Register Keycloak redirect URIs for `https://pilot.example.com/*` (realm
   **Clients → atlas-web → Valid redirect URIs**).

5. Start Caddy in front of the stack:

   ```bash
   docker compose \
     -f docker-compose.yml \
     -f docker-compose.pilot.yml \
     -f docker-compose.pilot.tls.yml \
     up -d --build caddy web backend keycloak
   ```

6. Smoke test:

   ```bash
   curl -sf https://pilot.example.com/api/auth/providers
   curl -sf https://auth.pilot.example.com/realms/atlas/.well-known/openid-configuration
   ```

## Notes

- Keep **backend** on the internal Docker network; only Caddy needs public 443.
- After TLS, use `https://pilot.example.com` everywhere (bookmarks, Kite redirect
  URIs if any, `APP_PUBLIC_URL`).
- Existing IP bookmarks will not share cookies with the HTTPS hostname.

See also [`oci-free-tier-pilot.md`](./oci-free-tier-pilot.md) §4 (TLS options).
