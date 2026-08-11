"""Unit tests for sandbox guest Agno / supertools compatibility shims."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest


def _sandbox_root() -> Path | None:
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "services" / "sandbox-python"
        if (candidate / "agno" / "utils" / "log.py").is_file():
            return candidate
    return None


SANDBOX_ROOT = _sandbox_root()

pytestmark = pytest.mark.skipif(
    SANDBOX_ROOT is None,
    reason="services/sandbox-python not available in this environment",
)


def _clear_shim_modules() -> None:
    for key in list(sys.modules):
        if (
            key == "agno"
            or key.startswith("agno.")
            or key == "supertools"
            or key.startswith("supertools.")
        ):
            del sys.modules[key]


@pytest.fixture()
def sandbox_path(monkeypatch: pytest.MonkeyPatch):
    assert SANDBOX_ROOT is not None
    monkeypatch.syspath_prepend(str(SANDBOX_ROOT))
    _clear_shim_modules()
    yield SANDBOX_ROOT
    _clear_shim_modules()


def test_log_helpers_importable(sandbox_path):
    agno_log = importlib.import_module("agno.utils.log")
    assert callable(agno_log.log_info)
    assert callable(agno_log.log_error)
    assert callable(agno_log.log_warning)
    assert callable(agno_log.log_debug)
    assert agno_log.logger is not None
    agno_log.log_info("hello")  # must not raise


def test_supertools_basetoolkit(sandbox_path):
    mod = importlib.import_module("supertools.common.base_tool")
    tk = mod.BaseToolkit(name="Freshdesk", tools=[])
    assert tk.name == "Freshdesk"
    assert tk.tools == []
    assert tk.pv == {}
    assert tk.settings == {}


def test_agno_basetoolkit_stores_tools(sandbox_path):
    tools = importlib.import_module("agno.tools")
    tk = tools.BaseToolkit(name="Freshdesk")
    assert tk.name == "Freshdesk"
    assert tk.tools == []

    @tk.register
    def list_tickets():
        return []

    assert list_tickets in tk.tools
    assert tools.Toolkit is tools.BaseToolkit
