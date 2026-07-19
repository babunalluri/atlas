"""Explicit registry for reviewed, source-controlled Python tools.

Adding a tool requires a code change and import here. Database values can select
only a registry key; they can never provide a module or callable path.
"""

from app.tools.custom.base import CustomToolSpec
from app.tools.custom.signed_rest import SIGNED_REST_SPEC

CUSTOM_TOOL_SPECS: tuple[CustomToolSpec, ...] = (SIGNED_REST_SPEC,)
CUSTOM_TOOL_BY_KEY = {spec.key: spec for spec in CUSTOM_TOOL_SPECS}

if len(CUSTOM_TOOL_BY_KEY) != len(CUSTOM_TOOL_SPECS):
    raise RuntimeError("Custom Python registry keys must be unique")
for registered_spec in CUSTOM_TOOL_SPECS:
    capability_names = [item.name for item in registered_spec.capabilities]
    if len(set(capability_names)) != len(capability_names):
        raise RuntimeError(
            f"Custom Python capabilities must be unique: {registered_spec.key}"
        )
    if not all(name.isidentifier() for name in capability_names):
        raise RuntimeError(
            f"Custom Python capability names must be identifiers: {registered_spec.key}"
        )


def public_custom_tool_catalog() -> list[dict[str, object]]:
    return [spec.public_dict() for spec in CUSTOM_TOOL_SPECS]
