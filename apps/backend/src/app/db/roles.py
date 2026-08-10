"""Ensure a non-superuser app role so Postgres RLS actually applies."""

from __future__ import annotations

import logging
import re
from urllib.parse import quote, urlparse

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.core.settings import Settings

logger = logging.getLogger(__name__)

APP_ROLE_NAME = "agent_saas_app"


def _escape_literal(value: str) -> str:
    return value.replace("'", "''")


def rewrite_database_url_user(url: str, username: str, password: str) -> str:
    """Replace the userinfo in a SQLAlchemy/asyncpg URL."""
    # Handle sqlalchemy dialects like postgresql+asyncpg://
    match = re.match(r"^([a-z0-9+]+)://([^/]+)(/.*)?$", url, flags=re.I)
    if not match:
        return url
    scheme, authority, path = match.group(1), match.group(2), match.group(3) or ""
    # authority may be user:pass@host:port
    if "@" in authority:
        hostport = authority.rsplit("@", 1)[1]
    else:
        hostport = authority
    userinfo = f"{quote(username, safe='')}:{quote(password, safe='')}"
    return f"{scheme}://{userinfo}@{hostport}{path}"


async def ensure_app_db_role(settings: Settings) -> None:
    """Create/update the app role and grant DML on public tables (idempotent).

    Must be run with the migration/owner connection (superuser or table owner).
    """
    migrate_url = settings.effective_migration_database_url
    if not migrate_url.startswith("postgresql"):
        return

    password = settings.database_app_password.get_secret_value() or _password_from_url(
        migrate_url
    )
    if not password:
        raise RuntimeError("Cannot ensure app DB role without a password")

    engine = create_async_engine(migrate_url, pool_pre_ping=True)
    try:
        async with engine.begin() as conn:
            role = APP_ROLE_NAME
            pwd = _escape_literal(password)
            await conn.execute(
                text(
                    f"""
                    DO $role$
                    BEGIN
                      IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
                        CREATE ROLE {role} LOGIN PASSWORD '{pwd}'
                          NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS INHERIT;
                      ELSE
                        ALTER ROLE {role} WITH LOGIN PASSWORD '{pwd}'
                          NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS INHERIT;
                      END IF;
                    END
                    $role$;
                    """
                )
            )
            db_name = conn.engine.url.database or "agent_saas"
            await conn.execute(text(f'GRANT CONNECT ON DATABASE "{db_name}" TO {role}'))
            await conn.execute(text(f"GRANT USAGE, CREATE ON SCHEMA public TO {role}"))
            # Agno PostgresDb persists sessions under schema "ai".
            await conn.execute(text("CREATE SCHEMA IF NOT EXISTS ai"))
            await conn.execute(text(f"ALTER SCHEMA ai OWNER TO {role}"))
            await conn.execute(text(f"GRANT USAGE, CREATE ON SCHEMA ai TO {role}"))
            await conn.execute(
                text(
                    f"GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA ai TO {role}"
                )
            )
            await conn.execute(
                text(f"GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA ai TO {role}")
            )
            await conn.execute(
                text(
                    f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {role}"
                )
            )
            await conn.execute(
                text(
                    f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {role}"
                )
            )
            # Future tables created by the migration/owner role.
            owner = conn.engine.url.username or "agent_saas"
            await conn.execute(
                text(
                    f"""
                    ALTER DEFAULT PRIVILEGES FOR ROLE {owner} IN SCHEMA public
                      GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {role}
                    """
                )
            )
            await conn.execute(
                text(
                    f"""
                    ALTER DEFAULT PRIVILEGES FOR ROLE {owner} IN SCHEMA public
                      GRANT USAGE, SELECT ON SEQUENCES TO {role}
                    """
                )
            )
            await conn.execute(
                text(
                    f"""
                    ALTER DEFAULT PRIVILEGES FOR ROLE {owner} IN SCHEMA ai
                      GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {role}
                    """
                )
            )
            await conn.execute(
                text(
                    f"""
                    ALTER DEFAULT PRIVILEGES FOR ROLE {owner} IN SCHEMA ai
                      GRANT USAGE, SELECT ON SEQUENCES TO {role}
                    """
                )
            )
        logger.info("Ensured Postgres app role %s (NOSUPERUSER, NOBYPASSRLS)", APP_ROLE_NAME)
    finally:
        await engine.dispose()


def _password_from_url(url: str) -> str:
    try:
        # Strip driver suffix for urlparse friendliness
        normalized = url.replace("postgresql+asyncpg://", "postgresql://").replace(
            "postgresql+psycopg://", "postgresql://"
        )
        parsed = urlparse(normalized)
        return parsed.password or ""
    except Exception:  # noqa: BLE001
        return ""


async def assert_runtime_db_role_safe(engine: AsyncEngine, settings: Settings) -> None:
    """Fail closed if the runtime connection can bypass RLS."""
    if not settings.database_url.startswith("postgresql"):
        return
    if settings.environment.lower() == "test":
        return

    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT current_user AS role,
                           rolsuper,
                           rolbypassrls
                    FROM pg_roles
                    WHERE rolname = current_user
                    """
                )
            )
        ).mappings().one()
        if bool(row["rolsuper"]) or bool(row["rolbypassrls"]):
            raise RuntimeError(
                "Refusing to start: database role "
                f"{row['role']!r} is superuser or has BYPASSRLS. "
                "Connect as agent_saas_app (see MIGRATION_DATABASE_URL vs DATABASE_URL)."
            )
