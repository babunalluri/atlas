"""Unit tests for the sandbox guest `requests` HttpProxy shim."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest


def _sandbox_root() -> Path | None:
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "services" / "sandbox-python"
        if (candidate / "requests" / "__init__.py").is_file():
            return candidate
    return None


SANDBOX_ROOT = _sandbox_root()

pytestmark = pytest.mark.skipif(
    SANDBOX_ROOT is None,
    reason="services/sandbox-python not available in this environment",
)


@pytest.fixture()
def requests_shim(monkeypatch: pytest.MonkeyPatch):
    """Import the guest shim without colliding with any installed requests."""
    assert SANDBOX_ROOT is not None
    monkeypatch.syspath_prepend(str(SANDBOX_ROOT))
    for key in list(sys.modules):
        if (
            key == "requests"
            or key.startswith("requests.")
            or key == "atlas_sdk"
            or key.startswith("atlas_sdk.")
        ):
            del sys.modules[key]
    return importlib.import_module("requests")


def test_response_shape_and_json(requests_shim):
    resp = requests_shim.Response(status_code=200, body={"id": 1, "name": "ok"})
    assert resp.ok is True
    assert resp.status_code == 200
    assert resp.json() == {"id": 1, "name": "ok"}
    assert "id" in resp.text
    resp.raise_for_status()  # must not raise


def test_response_raise_for_status(requests_shim):
    resp = requests_shim.Response(status_code=403, body="denied", ok=False)
    assert resp.ok is False
    with pytest.raises(requests_shim.HTTPError):
        resp.raise_for_status()


def test_get_routes_through_http_proxy(requests_shim):
    captured: dict = {}

    def fake_proxy(method, url, *, params=None, json_body=None, form_body=None, headers=None):
        captured.update(
            {
                "method": method,
                "url": url,
                "params": params,
                "json_body": json_body,
                "form_body": form_body,
                "headers": headers,
            }
        )
        return {"ok": True, "status_code": 200, "body": {"tickets": []}}

    requests_shim.http_proxy = fake_proxy
    resp = requests_shim.get(
        "https://api.freshdesk.com/api/v2/tickets",
        headers={"Content-Type": "application/json"},
        params={"per_page": 10},
        auth=("key", "X"),
    )

    assert resp.status_code == 200
    assert resp.json() == {"tickets": []}
    assert captured["method"] == "GET"
    assert captured["url"] == "https://api.freshdesk.com/api/v2/tickets"
    assert captured["params"] == {"per_page": 10}
    assert captured["headers"]["Authorization"].startswith("Basic ")
    assert captured["json_body"] is None


def test_post_json_and_dict_data(requests_shim):
    calls: list[dict] = []

    def fake_proxy(method, url, *, params=None, json_body=None, form_body=None, headers=None):
        calls.append(
            {"method": method, "url": url, "json_body": json_body, "form_body": form_body}
        )
        return {"ok": True, "status_code": 201, "body": {"created": True}}

    requests_shim.http_proxy = fake_proxy

    r1 = requests_shim.post("https://example.com/a", json={"name": "n"})
    r2 = requests_shim.post("https://example.com/b", data={"name": "n"})
    assert r1.status_code == 201 and r1.ok
    assert r2.json() == {"created": True}
    assert calls[0]["json_body"] == {"name": "n"}
    assert calls[1]["json_body"] == {"name": "n"}
    assert calls[0]["form_body"] is None
    assert calls[1]["form_body"] is None


def test_post_form_urlencoded_dict_data(requests_shim):
    calls: list[dict] = []

    def fake_proxy(method, url, *, params=None, json_body=None, form_body=None, headers=None):
        calls.append(
            {
                "method": method,
                "json_body": json_body,
                "form_body": form_body,
                "headers": headers,
            }
        )
        return {"ok": True, "status_code": 200, "body": {"status": "success"}}

    requests_shim.http_proxy = fake_proxy
    resp = requests_shim.post(
        "https://api.kite.trade/orders/regular",
        data={"tradingsymbol": "INFY", "exchange": "NSE", "quantity": 1},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp.ok
    assert calls[0]["json_body"] is None
    assert calls[0]["form_body"] == {
        "tradingsymbol": "INFY",
        "exchange": "NSE",
        "quantity": 1,
    }
