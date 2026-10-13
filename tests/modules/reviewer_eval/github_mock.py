"""A tiny in-memory GitHub, served through respx, for build / collect / cleanup tests."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import httpx
import respx

REPO = "acme/sandbox"
BASE = "https://api.github.com"


@dataclass
class FakeGitHub:
    login: str = "owner-user"
    next_number: int = 1
    pulls: dict[int, dict[str, Any]] = field(default_factory=dict)
    review_comments: dict[int, list[dict[str, Any]]] = field(default_factory=dict)
    reviews: dict[int, list[dict[str, Any]]] = field(default_factory=dict)
    issue_comments: dict[int, list[dict[str, Any]]] = field(default_factory=dict)
    deleted: list[str] = field(default_factory=list)
    posted: list[tuple[int, str]] = field(default_factory=list)
    review_requests: list[tuple[int, list[str]]] = field(default_factory=list)
    reject_review_requests: bool = False
    missing_branches: set[str] = field(default_factory=set)
    requests: list[tuple[str, str]] = field(default_factory=list)
    created_at: str = "2026-10-01T10:00:00Z"
    fail_with: int | None = None

    def install(self, router: respx.MockRouter) -> None:
        router.route(host="api.github.com").mock(side_effect=self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.requests.append((request.method, path))
        if self.fail_with is not None:
            return httpx.Response(self.fail_with, json={"message": "boom"})
        if path == "/user":
            return httpx.Response(200, json={"login": self.login})
        if path == f"/repos/{REPO}":
            return httpx.Response(200, json={"full_name": REPO})
        if request.method == "POST" and path == f"/repos/{REPO}/pulls":
            body = json.loads(request.content)
            number = self.next_number
            self.next_number += 1
            record = {
                "number": number,
                "html_url": f"https://github.com/{REPO}/pull/{number}",
                "created_at": self.created_at,
                **body,
            }
            self.pulls[number] = record
            return httpx.Response(201, json=record)
        if request.method == "POST" and path.endswith("/comments") and "/issues/" in path:
            self.posted.append((int(path.split("/")[5]), json.loads(request.content)["body"]))
            return httpx.Response(201, json={})
        if request.method == "POST" and path.endswith("/requested_reviewers"):
            if self.reject_review_requests:
                return httpx.Response(
                    422, json={"message": "Reviews may only be requested from collaborators"}
                )
            self.review_requests.append(
                (int(path.split("/")[5]), json.loads(request.content)["reviewers"])
            )
            return httpx.Response(201, json={})
        if request.method == "PATCH" and path.startswith(f"/repos/{REPO}/pulls/"):
            number = int(path.rsplit("/", 1)[1])
            self.pulls[number]["state"] = json.loads(request.content)["state"]
            return httpx.Response(200, json=self.pulls[number])
        if request.method == "GET" and path.startswith(f"/repos/{REPO}/pulls/"):
            parts = path.split("/")
            number = int(parts[5])
            if len(parts) == 6:
                if "diff" in request.headers.get("accept", ""):
                    return httpx.Response(200, text="diff --git a/x b/x\n")
                return httpx.Response(200, json=self.pulls[number])
            table = {"comments": self.review_comments, "reviews": self.reviews}[parts[6]]
            return httpx.Response(200, json=table.get(number, []))
        if request.method == "GET" and "/issues/" in path:
            number = int(path.split("/")[5])
            return httpx.Response(200, json=self.issue_comments.get(number, []))
        if request.method == "DELETE" and path.startswith(f"/repos/{REPO}/git/refs/heads/"):
            branch = path.removeprefix(f"/repos/{REPO}/git/refs/heads/")
            if branch in self.missing_branches:
                return httpx.Response(422, json={"message": "Reference does not exist"})
            self.deleted.append(branch)
            return httpx.Response(204)
        return httpx.Response(404, json={"message": f"no route {request.method} {path}"})


def inline(
    user: str, path: str, line: int, body: str = "this looks wrong", **extra: Any
) -> dict[str, Any]:
    return {
        "user": {"login": user},
        "path": path,
        "line": line,
        "body": body,
        "created_at": "2026-10-01T10:05:00Z",
        "side": "RIGHT",
        **extra,
    }
