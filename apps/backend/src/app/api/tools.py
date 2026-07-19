import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import (
    ToolDefinitionBase,
    ToolDefinitionCreateIn,
    ToolDefinitionOut,
    ToolDefinitionUpdateIn,
    ToolProviderCatalogOut,
    ToolValidationOut,
)
from app.auth.dependencies import require_roles
from app.core.settings import Settings, get_settings
from app.db.models import Role, ToolDefinition
from app.db.repositories import CredentialRepository, ToolDefinitionRepository
from app.db.session import tenant_session
from app.tenancy.context import TenantContext
from app.tools.custom import CUSTOM_TOOL_BY_KEY
from app.tools.providers import (
    PROVIDERS,
    custom_tool_catalog,
    provider_catalog,
    toolkit_catalog,
)
from app.tools.registry import SafeRestClient, UnsafeOutboundRequest
from app.tools.toolkit_catalog import TOOLKIT_BY_KEY, toolkit_availability

router = APIRouter(prefix="/admin/tools", tags=["admin-tools"])
AdminContext = Annotated[
    TenantContext, Depends(require_roles(Role.platform_admin, Role.tenant_admin))
]
TenantSession = Annotated[AsyncSession, Depends(tenant_session)]


def _out(row: ToolDefinition) -> ToolDefinitionOut:
    return ToolDefinitionOut(
        id=row.id,
        name=row.name,
        slug=row.slug,
        description=row.description,
        kind=row.kind,
        http_method=row.http_method,
        base_url=row.base_url,
        path=row.path,
        request_schema=row.request_schema,
        response_description=row.response_description,
        response_schema=row.response_schema,
        headers=row.headers,
        config=PROVIDERS.get(row.kind, PROVIDERS["http"]).redact_config(row.config),
        credential_id=row.credential_id,
        approval_required=row.approval_required,
        active=row.active,
        connection_status=row.connection_status,
        last_validated_at=row.last_validated_at,
        last_validation_error=row.last_validation_error,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def _validate_targets(kind: str, config: dict[str, Any], settings: Settings) -> None:
    urls: list[str] = []
    if kind == "http":
        urls.append(str(config["base_url"]))
    elif kind == "openapi":
        urls.extend(
            str(value)
            for value in (config.get("source_url"), config.get("base_url_override"))
            if value
        )
    elif kind == "mcp":
        urls.append(str(config["url"]))
    elif kind == "custom_python":
        spec = CUSTOM_TOOL_BY_KEY[str(config["custom_tool"])]
        parsed = spec.settings_model.model_validate(config.get("settings", {}))
        urls.extend(str(getattr(parsed, field_name)) for field_name in spec.url_fields)
    try:
        client = SafeRestClient(settings.allowed_outbound_hosts)
        for url in urls:
            await client.validate_url(url)
    except UnsafeOutboundRequest as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _row_values(row: ToolDefinition) -> dict[str, Any]:
    return {
        "name": row.name,
        "slug": row.slug,
        "description": row.description,
        "kind": row.kind,
        "http_method": row.http_method,
        "base_url": row.base_url,
        "path": row.path,
        "request_schema": row.request_schema,
        "response_description": row.response_description,
        "response_schema": row.response_schema,
        "headers": row.headers,
        "config": row.config,
        "credential_id": row.credential_id,
        "approval_required": row.approval_required,
        "active": row.active,
    }


async def _credential_headers(
    row: ToolDefinition,
    context: TenantContext,
    session: AsyncSession,
) -> dict[str, str]:
    if row.credential_id is None:
        return {}
    credential = await CredentialRepository(session, context).get(row.credential_id)
    if credential is None:
        raise HTTPException(status_code=422, detail="Credential not found for tenant")
    from app.agent_runtime.factory import AgentFactoryService

    header = str(row.config.get("credential_header", "Authorization"))
    prefix = str(row.config.get("credential_prefix", "Bearer "))
    return {
        header: prefix
        + AgentFactoryService._decrypt(credential.encrypted_value, credential.key_version)
    }


async def _validate_python_toolkit_credential(
    kind: str,
    config: dict[str, Any],
    credential_id: uuid.UUID | None,
    context: TenantContext,
    session: AsyncSession,
) -> None:
    if kind != "python_toolkit":
        return
    spec = TOOLKIT_BY_KEY[str(config["toolkit"])]
    available, reason = toolkit_availability(spec)
    if not available:
        raise HTTPException(status_code=422, detail=reason or "Toolkit is unavailable")
    if not spec.credentials:
        return
    if credential_id is None:
        raise HTTPException(
            status_code=422,
            detail=f"{spec.label} requires a '{spec.credentials[0].provider}' credential",
        )
    credential = await CredentialRepository(session, context).get(credential_id)
    if credential is None:
        raise HTTPException(status_code=422, detail="Credential not found for tenant")
    if credential.provider != spec.credentials[0].provider:
        raise HTTPException(
            status_code=422,
            detail=(f"{spec.label} requires credential provider '{spec.credentials[0].provider}'"),
        )


async def _validate_custom_python_credential(
    kind: str,
    config: dict[str, Any],
    credential_id: uuid.UUID | None,
    context: TenantContext,
    session: AsyncSession,
) -> None:
    if kind != "custom_python":
        return
    spec = CUSTOM_TOOL_BY_KEY[str(config["custom_tool"])]
    if spec.credential_provider is None:
        return
    if credential_id is None:
        raise HTTPException(
            status_code=422,
            detail=f"{spec.label} requires a '{spec.credential_provider}' credential",
        )
    credential = await CredentialRepository(session, context).get(credential_id)
    if credential is None:
        raise HTTPException(status_code=422, detail="Credential not found for tenant")
    if credential.provider != spec.credential_provider:
        raise HTTPException(
            status_code=422,
            detail=(
                f"{spec.label} requires credential provider "
                f"'{spec.credential_provider}'"
            ),
        )


async def _inspect_definition(
    row: ToolDefinition,
    context: TenantContext,
    session: AsyncSession,
    settings: Settings,
    *,
    test: bool,
) -> ToolValidationOut:
    provider = PROVIDERS[row.kind]
    client = SafeRestClient(
        settings.allowed_outbound_hosts,
        timeout_seconds=float(row.config.get("timeout_seconds", 10)),
    )
    headers = await _credential_headers(row, context, session)
    try:
        if test:
            result = await provider.test_connection(row.config, client, headers)
        else:
            capabilities = await provider.enumerate_tools(row.config, client, headers)
            from app.tools.providers import ConnectionResult

            result = ConnectionResult(
                ok=True, message="Capabilities enumerated", capabilities=capabilities
            )
    except (ValueError, RuntimeError) as exc:
        if test:
            row.connection_status = "failed"
            row.last_validated_at = datetime.now(UTC)
            row.last_validation_error = str(exc)[:500]
            await ToolDefinitionRepository(session, context).audit(
                action="tool.test",
                resource_type="tool_definition",
                resource_id=str(row.id),
                details={"ok": False, "provider": row.kind},
            )
            await session.flush()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if test:
        row.connection_status = "connected"
        row.last_validated_at = datetime.now(UTC)
        row.last_validation_error = None
        await ToolDefinitionRepository(session, context).audit(
            action="tool.test",
            resource_type="tool_definition",
            resource_id=str(row.id),
            details={
                "ok": True,
                "provider": row.kind,
                "capability_count": len(result.capabilities),
            },
        )
        await session.flush()
    return ToolValidationOut.model_validate(result.model_dump())


@router.get("/providers", response_model=list[ToolProviderCatalogOut])
async def list_providers(context: AdminContext) -> list[ToolProviderCatalogOut]:
    return [ToolProviderCatalogOut.model_validate(item) for item in provider_catalog()]


@router.get("/toolkits")
async def list_toolkits(context: AdminContext) -> list[dict[str, Any]]:
    return toolkit_catalog()


@router.get("/custom-python")
async def list_custom_python_tools(context: AdminContext) -> list[dict[str, object]]:
    return custom_tool_catalog()


@router.post("/validate", response_model=ToolValidationOut)
async def validate_tool(
    body: ToolDefinitionCreateIn,
    context: AdminContext,
    settings: Annotated[Settings, Depends(get_settings)],
) -> ToolValidationOut:
    await _validate_targets(body.kind, body.config, settings)
    provider = PROVIDERS[body.kind]
    capabilities = []
    if body.kind in {"http", "python_toolkit", "custom_python"}:
        capabilities = await provider.enumerate_tools(
            body.config, SafeRestClient(settings.allowed_outbound_hosts), {}
        )
    return ToolValidationOut(
        ok=True,
        message="Provider configuration is valid",
        capabilities=[item.model_dump() for item in capabilities],
    )


@router.get("", response_model=list[ToolDefinitionOut])
async def list_tools(context: AdminContext, session: TenantSession) -> list[ToolDefinitionOut]:
    return [_out(row) for row in await ToolDefinitionRepository(session, context).list()]


@router.post("", response_model=ToolDefinitionOut, status_code=201)
async def create_tool(
    body: ToolDefinitionCreateIn,
    context: AdminContext,
    session: TenantSession,
    settings: Annotated[Settings, Depends(get_settings)],
) -> ToolDefinitionOut:
    await _validate_targets(body.kind, body.config, settings)
    await _validate_python_toolkit_credential(
        body.kind, body.config, body.credential_id, context, session
    )
    await _validate_custom_python_credential(
        body.kind, body.config, body.credential_id, context, session
    )
    try:
        row = await ToolDefinitionRepository(session, context).create(body.model_dump())
    except LookupError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _out(row)


@router.get("/{tool_id}", response_model=ToolDefinitionOut)
async def get_tool(
    tool_id: uuid.UUID, context: AdminContext, session: TenantSession
) -> ToolDefinitionOut:
    row = await ToolDefinitionRepository(session, context).get(tool_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Tool not found")
    return _out(row)


@router.patch("/{tool_id}", response_model=ToolDefinitionOut)
async def update_tool(
    tool_id: uuid.UUID,
    body: ToolDefinitionUpdateIn,
    context: AdminContext,
    session: TenantSession,
    settings: Annotated[Settings, Depends(get_settings)],
) -> ToolDefinitionOut:
    repo = ToolDefinitionRepository(session, context)
    current = await repo.get(tool_id)
    if current is None:
        raise HTTPException(status_code=404, detail="Tool not found")
    changes = body.model_dump(exclude_unset=True)
    merged = ToolDefinitionBase.model_validate(_row_values(current) | changes)
    await _validate_targets(merged.kind, merged.config, settings)
    await _validate_python_toolkit_credential(
        merged.kind, merged.config, merged.credential_id, context, session
    )
    await _validate_custom_python_credential(
        merged.kind, merged.config, merged.credential_id, context, session
    )
    validated_changes = {key: value for key, value in merged.model_dump().items() if key in changes}
    if merged.kind == "http" and merged.http_method != "GET":
        validated_changes["approval_required"] = True
    try:
        row = await repo.update(tool_id, validated_changes)
    except LookupError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    assert row is not None
    return _out(row)


@router.post("/{tool_id}/test", response_model=ToolValidationOut)
async def test_tool(
    tool_id: uuid.UUID,
    context: AdminContext,
    session: TenantSession,
    settings: Annotated[Settings, Depends(get_settings)],
) -> ToolValidationOut:
    row = await ToolDefinitionRepository(session, context).get(tool_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Tool not found")
    return await _inspect_definition(row, context, session, settings, test=True)


@router.get("/{tool_id}/capabilities", response_model=ToolValidationOut)
async def enumerate_tool_capabilities(
    tool_id: uuid.UUID,
    context: AdminContext,
    session: TenantSession,
    settings: Annotated[Settings, Depends(get_settings)],
) -> ToolValidationOut:
    row = await ToolDefinitionRepository(session, context).get(tool_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Tool not found")
    return await _inspect_definition(row, context, session, settings, test=False)


@router.delete("/{tool_id}", status_code=204)
async def delete_tool(
    tool_id: uuid.UUID, context: AdminContext, session: TenantSession
) -> Response:
    try:
        deleted = await ToolDefinitionRepository(session, context).delete(tool_id)
    except IntegrityError as exc:
        raise HTTPException(
            status_code=409,
            detail="Tool is attached to an agent and cannot be deleted; deactivate it instead",
        ) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="Tool not found")
    return Response(status_code=204)
