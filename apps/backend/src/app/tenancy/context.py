import uuid
from contextvars import ContextVar, Token
from dataclasses import dataclass, field

from app.db.models import Role


@dataclass(frozen=True, slots=True)
class TenantContext:
    tenant_id: uuid.UUID
    user_id: str
    role: Role
    clerk_org_id: str
    scopes: tuple[str, ...] = field(default_factory=tuple)
    principal_type: str = "user"

    def can_administer(self) -> bool:
        return self.role in {Role.platform_admin, Role.tenant_admin} or self.has_scope(
            "agent_os:admin"
        )

    def can_approve(self) -> bool:
        return self.can_administer()

    def has_scope(self, required: str) -> bool:
        return "agent_os:admin" in self.scopes or "*" in self.scopes or required in self.scopes


_tenant_context: ContextVar[TenantContext | None] = ContextVar("tenant_context", default=None)


def set_tenant_context(context: TenantContext) -> Token[TenantContext | None]:
    return _tenant_context.set(context)


def reset_tenant_context(token: Token[TenantContext | None]) -> None:
    _tenant_context.reset(token)


def current_tenant() -> TenantContext:
    context = _tenant_context.get()
    if context is None:
        raise RuntimeError("Tenant context is not initialized")
    return context


def current_tenant_or_none() -> TenantContext | None:
    return _tenant_context.get()
