from fastapi import HTTPException
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from app.auth.dependencies import clear_tenant_context, require_tenant
from app.core.settings import get_settings


def _service_account_scope(method: str, path: str) -> str | None:
    # Agents are not top-level external targets — only teams and workflows.
    if path.startswith("/v1/agents/") and path.endswith("/runs"):
        return None
    if path.startswith("/v1/teams/") and path.endswith("/runs"):
        return "teams:run"
    if path.startswith("/v1/workflows/") and path.endswith("/runs"):
        return "workflows:run"
    if path == "/api/v1/public/runs" or path.startswith("/api/v1/public/"):
        return "teams:run"
    if path.startswith("/api/sessions"):
        return "sessions:delete" if method == "DELETE" else "sessions:read"
    if path.startswith("/admin/service-accounts"):
        return {
            "GET": "service_accounts:read",
            "POST": "service_accounts:write",
            "DELETE": "service_accounts:delete",
        }.get(method)
    if path.startswith("/api/admin/traces"):
        return "traces:read"
    if path.startswith(("/admin/", "/api/admin/", "/api/approvals", "/api/knowledge")):
        return "agent_os:admin"
    if path.startswith("/mcp"):
        return "mcp:access"
    return None


class TenantAuthMiddleware(BaseHTTPMiddleware):
    """Authenticate requests and attach TenantContext before AgentOS handlers run."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        public_paths: set[str] | None = None,
    ) -> None:
        super().__init__(app)
        self.public_paths = public_paths or {
            "/health",
            "/docs",
            "/openapi.json",
            "/redoc",
            "/favicon.ico",
        }

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path
        if (
            path in self.public_paths
            or path.startswith("/docs")
            or path.startswith("/public/tenants/")
            or path.startswith("/public/t/")
            or path.startswith("/public/webhooks/")
            or path.startswith("/public/email/")
            or path.startswith("/api/billing/webhooks/")
            or path.startswith("/admin/onboarding")
            or path.startswith("/internal/sandbox/")
        ):
            return await call_next(request)

        settings = get_settings()
        try:
            context = await require_tenant(
                request,
                authorization=request.headers.get("authorization"),
                x_platform_tenant_id=request.headers.get("x-platform-tenant-id"),
                settings=settings,
            )
            request.state.tenant = context
            if context.principal_type == "service_account":
                required_scope = _service_account_scope(request.method, path)
                if required_scope is None or not context.has_scope(required_scope):
                    clear_tenant_context(request)
                    return JSONResponse(
                        status_code=403,
                        content={"detail": "Service account scope is insufficient"},
                    )
            # Generic AgentOS storage/control-plane routes do not carry this
            # product's tenant_id columns. Keep them private and expose only
            # explicitly tenant-filtered product APIs below /api and /admin.
            private_agentos_prefixes = (
                "/sessions",
                "/memories",
                "/learnings",
                "/approvals",
                "/traces",
                "/schedules",
                "/agents/tenant-agent",
                "/teams/tenant-team",
                "/workflows/tenant-workflow",
            )
            if path.startswith(private_agentos_prefixes):
                return JSONResponse(status_code=404, content={"detail": "Not found"})
            # Builder / control-plane routes are for tenant admins only.
            # End users run assigned workflows via /api/workflows/available and
            # /t/{slug}/chat — not the admin catalogs.
            admin_only_prefixes = (
                "/metrics",
                "/evals",
                "/knowledge",
                "/database",
                "/service-accounts",
                "/admin/agents",
                "/admin/teams",
                "/admin/workflows",
                "/admin/tools",
                "/admin/users",
                "/admin/notifications",
                "/admin/vault",
                "/admin/billing",
                "/admin/customers",
                "/admin/credentials",
                "/admin/channels",
                "/admin/knowledge",
                "/admin/integrations",
                "/admin/mcp",
                "/admin/public-api",
                "/admin/evals",
                "/admin/metrics",
                "/admin/schedules",
                "/admin/approvals",
                "/admin/traces",
                "/admin/service-accounts",
                "/admin/platform",
                "/api/admin/",
                "/api/approvals",
                "/api/knowledge",
            )
            if path.startswith(admin_only_prefixes) and not context.can_administer():
                clear_tenant_context(request)
                return JSONResponse(
                    status_code=403,
                    content={"detail": "Tenant administrator role required"},
                )
            # Do not clear ContextVar before streaming bodies finish; request tasks are isolated.
            return await call_next(request)
        except HTTPException as exc:
            clear_tenant_context(request)
            return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
