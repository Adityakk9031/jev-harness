"""A small GitHub REST client built on httpx. No SDK; every request and status is visible here."""

from __future__ import annotations

import asyncio
import re
import shutil
import subprocess
from datetime import UTC, datetime
from typing import Any

import httpx

from jev_harness.core.errors import ConfigError, HarnessError

DEFAULT_API = "https://api.github.com"
API_VERSION = "2022-11-28"
MAX_PAGES = 20
RETRY_STATUSES = {502, 503, 504}
SLUG = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class GitHubError(HarnessError):
    """A GitHub API call failed; the message never contains the token."""


def validate_slug(value: str) -> str:
    if not SLUG.match(value):
        raise ConfigError(f"--target-repo must look like owner/name, got {value!r}")
    return value


def resolve_token(configured: str | None) -> str:
    """The token from ``GITHUB_TOKEN`` (via settings) or ``gh auth token``."""
    if configured:
        return configured
    if shutil.which("gh") is not None:
        completed = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        token = completed.stdout.strip()
        if completed.returncode == 0 and token:
            return token
    raise ConfigError(
        "no GitHub token: set GITHUB_TOKEN or sign in with `gh auth login` "
        "(the token needs repo access to the sandbox repository only)"
    )


class GitHubClient:
    def __init__(
        self,
        token: str,
        *,
        api_base: str = DEFAULT_API,
        client: httpx.AsyncClient | None = None,
        timeout: float = 30.0,
        retry_delay: float = 1.0,
    ) -> None:
        self._token = token
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(base_url=api_base, timeout=timeout)
        self._retry_delay = retry_delay
        self._login: str | None = None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> GitHubClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    def _headers(self, accept: str = "application/vnd.github+json") -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": accept,
            "X-GitHub-Api-Version": API_VERSION,
        }

    def _describe(self, response: httpx.Response) -> str:
        try:
            message = response.json().get("message", "")
        except ValueError:
            message = response.text[:200]
        text = f"GitHub returned {response.status_code} for {response.request.method} " + (
            f"{response.request.url.path}: {message}"
        )
        return text.replace(self._token, "<token>")

    async def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: dict[str, Any] | None = None,
        accept: str = "application/vnd.github+json",
    ) -> httpx.Response:
        for attempt in range(3):
            try:
                response = await self._client.request(
                    method, path, json=json, params=params, headers=self._headers(accept)
                )
            except httpx.HTTPError as exc:
                if attempt == 2:
                    raise GitHubError(f"could not reach GitHub: {type(exc).__name__}") from exc
                await asyncio.sleep(self._retry_delay)
                continue
            if response.status_code in RETRY_STATUSES and attempt < 2:
                await asyncio.sleep(self._retry_delay)
                continue
            if response.status_code == 401:
                raise GitHubError("GitHub rejected the token (401); check GITHUB_TOKEN")
            if response.status_code == 403 and response.headers.get("x-ratelimit-remaining") == "0":
                reset = response.headers.get("x-ratelimit-reset", "")
                when = (
                    datetime.fromtimestamp(int(reset), UTC).isoformat()
                    if reset.isdigit()
                    else "later"
                )
                raise GitHubError(f"GitHub rate limit reached; resets {when}")
            if response.status_code >= 400:
                raise GitHubError(self._describe(response))
            return response
        raise GitHubError("unreachable")  # pragma: no cover

    async def paginate(
        self, path: str, params: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        query: dict[str, Any] | None = {"per_page": 100, **(params or {})}
        next_path: str | None = path
        for _ in range(MAX_PAGES):
            if next_path is None:
                break
            response = await self.request("GET", next_path, params=query)
            page = response.json()
            if isinstance(page, list):
                items.extend(p for p in page if isinstance(p, dict))
            link = response.headers.get("link", "")
            match = re.search(r'<([^>]+)>;\s*rel="next"', link)
            next_path = match.group(1) if match else None
            query = None  # the next URL already carries its query string
        return items

    async def authenticated_login(self) -> str:
        if self._login is None:
            body = (await self.request("GET", "/user")).json()
            self._login = str(body.get("login", ""))
        return self._login

    async def get_repo(self, repo: str) -> dict[str, Any]:
        result = (await self.request("GET", f"/repos/{repo}")).json()
        return result if isinstance(result, dict) else {}

    async def create_pull(
        self, repo: str, *, title: str, head: str, base: str, body: str
    ) -> dict[str, Any]:
        payload = {"title": title, "head": head, "base": base, "body": body}
        return dict((await self.request("POST", f"/repos/{repo}/pulls", json=payload)).json())

    async def pull(self, repo: str, number: int) -> dict[str, Any]:
        return dict((await self.request("GET", f"/repos/{repo}/pulls/{number}")).json())

    async def pull_diff(self, repo: str, number: int) -> str:
        response = await self.request(
            "GET", f"/repos/{repo}/pulls/{number}", accept="application/vnd.github.diff"
        )
        return response.text

    async def review_comments(self, repo: str, number: int) -> list[dict[str, Any]]:
        return await self.paginate(f"/repos/{repo}/pulls/{number}/comments")

    async def reviews(self, repo: str, number: int) -> list[dict[str, Any]]:
        return await self.paginate(f"/repos/{repo}/pulls/{number}/reviews")

    async def issue_comments(self, repo: str, number: int) -> list[dict[str, Any]]:
        return await self.paginate(f"/repos/{repo}/issues/{number}/comments")

    async def close_pull(self, repo: str, number: int) -> None:
        await self.request("PATCH", f"/repos/{repo}/pulls/{number}", json={"state": "closed"})

    async def delete_branch(self, repo: str, branch: str) -> bool:
        """Delete a branch; a branch that is already gone is not an error."""
        try:
            await self.request("DELETE", f"/repos/{repo}/git/refs/heads/{branch}")
        except GitHubError as exc:
            if "404" in str(exc) or "422" in str(exc):
                return False
            raise
        return True
