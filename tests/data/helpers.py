"""Shared helpers for the data tests: fixture loading and a fake ``urlopen``."""

from __future__ import annotations

import io
import json
import urllib.error
from email.message import Message
from pathlib import Path
from typing import Any

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def fixture_bytes(name: str) -> bytes:
    """Raw bytes of a recorded reply."""
    return (FIXTURES / name).read_bytes()


def fixture_json(name: str) -> Any:
    """Parsed recorded reply."""
    return json.loads(fixture_bytes(name))


class FakeResponse(io.BytesIO):
    """Enough of ``http.client.HTTPResponse`` for the fetcher."""

    def __init__(self, body: bytes, headers: dict[str, str] | None = None) -> None:
        super().__init__(body)
        self.headers = Message()
        for k, v in (headers or {}).items():
            self.headers[k] = v


def http_error(
    url: str, code: int, reason: str = "err", headers: dict[str, str] | None = None
) -> urllib.error.HTTPError:
    """An HTTPError like urllib raises."""
    msg = Message()
    for k, v in (headers or {}).items():
        msg[k] = v
    return urllib.error.HTTPError(url, code, reason, msg, io.BytesIO(b"{}"))


class FakeOpener:
    """Replays a script of replies; each entry is bytes, an exception, or a callable(url) -> either."""

    def __init__(self, script: list[Any]) -> None:
        self.script = list(script)
        self.urls: list[str] = []
        self.headers: list[dict[str, str]] = []

    def __call__(self, req: Any, timeout: float) -> FakeResponse:
        self.urls.append(req.full_url)
        self.headers.append({k.lower(): v for k, v in req.header_items()})
        if not self.script:
            raise AssertionError(f"unexpected request {req.full_url}")
        item = self.script.pop(0)
        if callable(item) and not isinstance(item, bytes):
            item = item(req.full_url)
        if isinstance(item, BaseException):
            raise item
        return FakeResponse(item)
