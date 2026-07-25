"""Compatibility aliases for `from requests.exceptions import …`."""

from __future__ import annotations

from typing import Any

__all__ = ["HTTPError", "RequestException"]


class RequestException(Exception):
    """Base error for the requests shim."""


class HTTPError(RequestException):
    def __init__(self, *args: Any, response: Any = None) -> None:
        super().__init__(*args)
        self.response = response
