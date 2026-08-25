"""A very small GitHub REST client.

Standard library only, like the scripts this repository has always shipped: the
application should install and run inside a GitHub Actions job without a wheel
download, and an HTTP client is not where this project earns its keep.

The transport is a protocol, so tests never touch the network.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

logger = logging.getLogger(__name__)

USER_AGENT = "article-reminders"
API_VERSION = "2022-11-28"


class GitHubError(RuntimeError):
    """A GitHub request failed in a way the caller has to know about."""

    def __init__(self, status: int, method: str, path: str, body: str) -> None:
        super().__init__(f"{method} {path} -> {status}: {body[:400]}")
        self.status = status
        self.method = method
        self.path = path
        self.body = body


@dataclass(frozen=True, slots=True)
class Response:
    """A decoded GitHub response."""

    status: int
    data: Any

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


class Transport(Protocol):
    """How a request actually goes out. Substituted wholesale in tests."""

    def send(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        payload: bytes | None,
    ) -> Response: ...


class UrllibTransport:
    """The real transport."""

    def __init__(self, timeout: float = 30.0) -> None:
        self._timeout = timeout

    def send(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        payload: bytes | None,
    ) -> Response:
        request = urllib.request.Request(
            url=url, data=payload, headers=dict(headers), method=method
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                body = response.read().decode("utf-8")
                return Response(response.status, json.loads(body) if body else None)
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                data = json.loads(raw) if raw else None
            except json.JSONDecodeError:
                data = {"message": raw}
            return Response(exc.code, data)
        except urllib.error.URLError as exc:
            raise GitHubError(0, method, url, str(exc.reason)) from exc


class GitHubClient:
    """Authenticated access to one GitHub API host."""

    def __init__(
        self,
        token: str | None,
        *,
        api_url: str = "https://api.github.com",
        transport: Transport | None = None,
    ) -> None:
        self._token = token
        self._api_url = api_url.rstrip("/")
        self._transport = transport or UrllibTransport()

    @property
    def is_authenticated(self) -> bool:
        return bool(self._token)

    def request(
        self,
        method: str,
        path: str,
        payload: Mapping[str, Any] | None = None,
        *,
        tolerate: tuple[int, ...] = (),
    ) -> Response:
        """Send a request.

        Statuses in ``tolerate`` come back as a :class:`Response` for the caller to
        inspect; anything else outside 2xx raises. Failing loudly by default is on
        purpose: a silently swallowed 404 turns into a portfolio that quietly stops
        detecting activity.
        """
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": USER_AGENT,
            "X-GitHub-Api-Version": API_VERSION,
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        body: bytes | None = None
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"

        url = path if path.startswith("http") else f"{self._api_url}{path}"
        response = self._transport.send(method, url, headers=headers, payload=body)
        if response.ok or response.status in tolerate:
            return response
        raise GitHubError(response.status, method, path, json.dumps(response.data))

    def get(self, path: str, *, tolerate: tuple[int, ...] = ()) -> Response:
        return self.request("GET", path, tolerate=tolerate)

    def post(
        self, path: str, payload: Mapping[str, Any], *, tolerate: tuple[int, ...] = ()
    ) -> Response:
        return self.request("POST", path, payload, tolerate=tolerate)

    def patch(
        self, path: str, payload: Mapping[str, Any], *, tolerate: tuple[int, ...] = ()
    ) -> Response:
        return self.request("PATCH", path, payload, tolerate=tolerate)
