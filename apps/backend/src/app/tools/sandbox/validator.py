"""AST validation for tenant-authored sandboxed Python tools."""

from __future__ import annotations

import ast
import re
from typing import Any

SAFE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")

# Deny-list only: any other import root is allowed at save time.
# Missing packages fail at runtime ImportError inside the guest image.
DENIED_MODULES = frozenset(
    {
        "os",
        "sys",
        "subprocess",
        "socket",
        "ssl",
        "ctypes",
        "multiprocessing",
        "threading",
        "shutil",
        "tempfile",
        "importlib",
        "builtins",
        "pickle",
        "marshal",
        "code",
        "codeop",
        "pty",
        "fcntl",
        "resource",
        "signal",
        "http",  # http.client direct net; guest has no network anyway
        "httpx",
        "aiohttp",
    }
)

DENIED_NAMES = frozenset(
    {
        "eval",
        "exec",
        "compile",
        "open",
        "input",
        "breakpoint",
        "__import__",
        "__builtins__",
        "globals",
        "locals",
        "vars",
        "getattr",
        "setattr",
        "delattr",
        "memoryview",
        "exit",
        "quit",
        "help",
        "copyright",
        "credits",
        "license",
    }
)

# Only dangerous dunder *attribute* loads/stores. Normal method defs and
# calls like ``super().__init__(...)`` / ``self.__str__()`` are allowed.
DENIED_DUNDERS = frozenset(
    {
        "__import__",
        "__builtins__",
        "__loader__",
        "__spec__",
        "__subclasses__",
        "__bases__",
        "__mro__",
        "__globals__",
        "__code__",
        "__closure__",
        "__func__",
        "__self__",
        "__dict__",
        "__class__",
        "__getattribute__",
        "__setattr__",
        "__delattr__",
        "__reduce__",
        "__reduce_ex__",
    }
)

_TOOLKIT_BASE_NAMES = frozenset({"BaseToolkit", "Toolkit"})

MAX_SOURCE_CHARS = 80_000


class SandboxValidationError(ValueError):
    pass


_SKIP_PARAMS = frozenset({"self", "cls", "ctx", "context"})


def validate_tenant_python_source(source: str) -> list[str]:
    """Return capability names (top-level async defs + BaseToolkit methods)."""
    return [item["name"] for item in discover_capability_specs(source)]


def discover_capability_specs(source: str) -> list[dict[str, Any]]:
    """Return capability specs with input schemas extracted from source AST."""
    if not source or not source.strip():
        raise SandboxValidationError("Source code is required")
    if len(source) > MAX_SOURCE_CHARS:
        raise SandboxValidationError(f"Source exceeds {MAX_SOURCE_CHARS} characters")
    try:
        tree = ast.parse(source, filename="<tenant_python>")
    except SyntaxError as exc:
        raise SandboxValidationError(f"Syntax error: {exc.msg} (line {exc.lineno})") from exc

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            _check_import(node)
        elif isinstance(node, ast.Call):
            _check_call(node)
        elif isinstance(node, ast.Attribute) and node.attr in DENIED_DUNDERS:
            raise SandboxValidationError(f"Dunder attribute access is forbidden: {node.attr}")
        elif isinstance(node, ast.Name) and node.id in DENIED_NAMES:
            raise SandboxValidationError(f"Forbidden builtin: {node.id}")

    capabilities = _discover_capability_specs(tree)
    if not capabilities:
        raise SandboxValidationError(_missing_capability_message(tree))
    return capabilities


def _discover_capabilities(tree: ast.Module) -> list[str]:
    """Top-level async defs plus public methods on BaseToolkit subclasses."""
    return [item["name"] for item in _discover_capability_specs(tree)]


def _discover_capability_specs(tree: ast.Module) -> list[dict[str, Any]]:
    """Top-level async defs plus public methods on BaseToolkit subclasses."""
    specs: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _add(fn: ast.AsyncFunctionDef | ast.FunctionDef) -> None:
        name = fn.name
        if name in seen:
            return
        if not SAFE_NAME.fullmatch(name) or name.startswith("_"):
            return
        seen.add(name)
        doc = ast.get_docstring(fn) or name.replace("_", " ")
        specs.append(
            {
                "name": name,
                "description": doc.strip().split("\n\n", 1)[0][:2000],
                "input_schema": _input_schema_from_function(fn),
            }
        )

    for node in tree.body:
        if isinstance(node, ast.AsyncFunctionDef):
            _add(node)
        elif isinstance(node, ast.ClassDef) and _inherits_toolkit(node):
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if item.name in {"__init__", "__new__"}:
                        continue
                    _add(item)
    return specs


def _annotation_type_name(annotation: ast.expr | None) -> str | None:
    if annotation is None:
        return None
    if isinstance(annotation, ast.Name):
        return annotation.id
    if isinstance(annotation, ast.Attribute):
        return annotation.attr
    if isinstance(annotation, ast.Subscript):
        return _annotation_type_name(annotation.value)
    if isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):
        # PEP 604 unions: str | None → prefer the non-None side
        left = _annotation_type_name(annotation.left)
        right = _annotation_type_name(annotation.right)
        if left == "None":
            return right
        if right == "None":
            return left
        return left or right
    return None


def _annotation_to_json_type(annotation: ast.expr | None) -> str:
    name = _annotation_type_name(annotation)
    if not name:
        return "string"
    lowered = name.lower()
    if lowered in {"optional", "union"}:
        # Optional[X] / Union[X, None] — dig into subscript slice when present
        if isinstance(annotation, ast.Subscript):
            slice_node = annotation.slice
            if isinstance(slice_node, ast.Tuple) and slice_node.elts:
                for elt in slice_node.elts:
                    inner = _annotation_type_name(elt)
                    if inner and inner != "None":
                        return _annotation_to_json_type(elt)
            else:
                return _annotation_to_json_type(slice_node)
        return "string"
    if lowered in {"int", "integer"}:
        return "integer"
    if lowered in {"float", "number"}:
        return "number"
    if lowered in {"bool", "boolean"}:
        return "boolean"
    if lowered in {"list", "tuple", "set"}:
        return "array"
    if lowered in {"dict", "mapping", "object"}:
        return "object"
    return "string"


def _input_schema_from_function(
    node: ast.AsyncFunctionDef | ast.FunctionDef,
) -> dict[str, Any]:
    """Build a JSON Schema object from a function/method signature."""
    properties: dict[str, Any] = {}
    required: list[str] = []
    args = node.args
    positional = list(args.args)
    defaults = list(args.defaults)
    first_default = len(positional) - len(defaults)

    for index, arg in enumerate(positional):
        name = arg.arg
        if name in _SKIP_PARAMS:
            continue
        properties[name] = {
            "type": _annotation_to_json_type(arg.annotation),
            "description": name.replace("_", " "),
        }
        if index < first_default:
            required.append(name)

    for index, arg in enumerate(args.kwonlyargs):
        name = arg.arg
        if name in _SKIP_PARAMS:
            continue
        properties[name] = {
            "type": _annotation_to_json_type(arg.annotation),
            "description": name.replace("_", " "),
        }
        default = args.kw_defaults[index] if index < len(args.kw_defaults) else None
        if default is None:
            required.append(name)

    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


def _base_name(base: ast.expr) -> str:
    if isinstance(base, ast.Name):
        return base.id
    if isinstance(base, ast.Attribute):
        return base.attr
    return ""


def _inherits_toolkit(node: ast.ClassDef) -> bool:
    return any(_base_name(base) in _TOOLKIT_BASE_NAMES for base in node.bases)


def _looks_like_toolkit_class(tree: ast.AST) -> bool:
    if not isinstance(tree, ast.Module):
        return False
    return any(
        isinstance(node, ast.ClassDef) and _inherits_toolkit(node) for node in tree.body
    )


def _missing_capability_message(tree: ast.AST) -> str:
    if _looks_like_toolkit_class(tree):
        return (
            "No public methods found on BaseToolkit/Toolkit subclasses. "
            "Define public methods on the toolkit class "
            "(e.g. def list_tickets(self, ...)) or top-level "
            "async def capability(ctx, ...)."
        )
    return (
        "Declare at least one top-level async capability function "
        "(e.g. async def list_items(ctx, ...)) or a BaseToolkit subclass "
        "with public methods."
    )


def validate_dependencies(
    dependencies: list[dict[str, Any]],
    allowlist: set[tuple[str, str]],
) -> list[dict[str, str]]:
    """Ensure each dependency is an active platform allowlist pin."""
    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in dependencies:
        name = str(item.get("name", "")).strip().lower()
        version = str(item.get("version", "")).strip()
        if not name or not version:
            raise SandboxValidationError("Each dependency needs name and version")
        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,99}", name):
            raise SandboxValidationError(f"Invalid package name: {name}")
        if name in seen:
            raise SandboxValidationError(f"Duplicate dependency: {name}")
        if (name, version) not in allowlist:
            raise SandboxValidationError(
                f"Package {name}=={version} is not on the platform allowlist"
            )
        seen.add(name)
        normalized.append({"name": name, "version": version})
    return normalized


def _import_root_allowed(root: str) -> bool:
    """Deny dangerous modules only; all other roots are allowed at save time."""
    return root not in DENIED_MODULES


def _check_import(node: ast.Import | ast.ImportFrom) -> None:
    if isinstance(node, ast.Import):
        for alias in node.names:
            root = alias.name.split(".", 1)[0]
            if not _import_root_allowed(root):
                raise SandboxValidationError(f"Import not allowed: {alias.name}")
        return
    if node.module is None:
        raise SandboxValidationError("Relative imports are not allowed")
    root = node.module.split(".", 1)[0]
    if not _import_root_allowed(root):
        raise SandboxValidationError(f"Import not allowed: {node.module}")


def _check_call(node: ast.Call) -> None:
    func = node.func
    if isinstance(func, ast.Name) and func.id in DENIED_NAMES:
        raise SandboxValidationError(f"Forbidden call: {func.id}")
    if isinstance(func, ast.Name) and func.id == "getattr":
        raise SandboxValidationError("Forbidden call: getattr")
