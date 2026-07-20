"""AST validation for tenant-authored sandboxed Python tools."""

from __future__ import annotations

import ast
import re
from typing import Any

SAFE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")

ALLOWED_STDLIB = frozenset(
    {
        "asyncio",
        "collections",
        "dataclasses",
        "datetime",
        "decimal",
        "enum",
        "functools",
        "hashlib",
        "itertools",
        "json",
        "math",
        "re",
        "statistics",
        "string",
        "typing",
        "uuid",
    }
)
ALLOWED_THIRD_PARTY = frozenset({"jsonschema", "pydantic"})
ALLOWED_ROOTS = ALLOWED_STDLIB | ALLOWED_THIRD_PARTY | frozenset({"atlas_sdk"})

DENIED_NAMES = frozenset(
    {
        "eval",
        "exec",
        "compile",
        "open",
        "input",
        "breakpoint",
        "__import__",
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
        "pathlib",
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
        "http",
        "urllib",
        "requests",
        "httpx",
        "aiohttp",
    }
)

MAX_SOURCE_CHARS = 80_000


class SandboxValidationError(ValueError):
    pass


def validate_tenant_python_source(source: str) -> list[str]:
    """Return declared async capability function names after AST checks."""
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
        elif isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            raise SandboxValidationError(f"Dunder attribute access is forbidden: {node.attr}")
        elif isinstance(node, ast.Name) and node.id in DENIED_NAMES:
            raise SandboxValidationError(f"Forbidden builtin: {node.id}")
        elif isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name.startswith(
            "_"
        ):
            # Private helpers are allowed; capabilities are public names.
            continue

    capabilities = [
        node.name
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef) and SAFE_NAME.fullmatch(node.name)
        and not node.name.startswith("_")
    ]
    if not capabilities:
        raise SandboxValidationError(
            "Declare at least one top-level async capability function "
            "(e.g. async def list_items(ctx, ...))"
        )
    return capabilities


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
        if name not in ALLOWED_THIRD_PARTY and name not in ALLOWED_STDLIB:
            # Allowlist may grow beyond the static import set after image rebuild;
            # still require membership in platform table (checked above).
            pass
        seen.add(name)
        normalized.append({"name": name, "version": version})
    return normalized


def _check_import(node: ast.Import | ast.ImportFrom) -> None:
    if isinstance(node, ast.Import):
        for alias in node.names:
            root = alias.name.split(".", 1)[0]
            if root in DENIED_MODULES or root not in ALLOWED_ROOTS:
                raise SandboxValidationError(f"Import not allowed: {alias.name}")
        return
    if node.module is None:
        raise SandboxValidationError("Relative imports are not allowed")
    root = node.module.split(".", 1)[0]
    if root in DENIED_MODULES or root not in ALLOWED_ROOTS:
        raise SandboxValidationError(f"Import not allowed: {node.module}")


def _check_call(node: ast.Call) -> None:
    func = node.func
    if isinstance(func, ast.Name) and func.id in DENIED_NAMES:
        raise SandboxValidationError(f"Forbidden call: {func.id}")
    if isinstance(func, ast.Name) and func.id == "getattr":
        raise SandboxValidationError("Forbidden call: getattr")
