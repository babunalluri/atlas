# Atlas Keycloak themes

Custom login and account themes that mirror Atlas web tokens (Syne + IBM Plex Sans,
teal accent, canvas gradient). Based on Keycloak’s [theme system](https://www.keycloak.org/ui-customization/themes).

## Layout

```text
themes/atlas/
  login/     → sign-in, register, reset password (extends keycloak.v2)
  account/   → end-user account console (extends keycloak.v3)
```

Realm `atlas` sets `loginTheme` and `accountTheme` to `atlas` in `atlas-realm.json`.

## Local Compose

Themes are mounted read-only in `docker-compose.yml`:

```yaml
- ./infra/keycloak/themes:/opt/keycloak/themes:ro
```

Dev cache is disabled so CSS edits apply after a Keycloak restart:

```bash
docker compose restart keycloak
```

## Existing Keycloak data volume

Realm import runs only on **first** startup. If you already have a `keycloak_data` volume:

1. **Admin UI:** Realm **atlas** → **Realm settings** → **Themes** → set Login + Account to `atlas`, Save.
2. **Or** reset dev data: `docker compose down` then `docker volume rm atlas_keycloak_data` (destroys IdP users/realm state).

## Production

Copy `infra/keycloak/themes/atlas` into your Keycloak image or mount the directory on OKE/VM deployments. Re-enable theme caching in production for performance.
