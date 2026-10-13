"""The slice of the GitHub API the hub uses, behind a protocol so tests need no network."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

import httpx
from pydantic import BaseModel, Field

API = "https://api.github.com"


class PullRequest(BaseModel):
    number: int
    title: str
    url: str
    created_at: datetime
    labels: list[str] = Field(default_factory=list)


class Issue(BaseModel):
    number: int
    title: str
    body: str = ""


class GitHub(Protocol):
    def find_repos(self, owner: str, topic: str) -> list[str]: ...
    def pages_source(self, repo: str) -> str | None: ...
    def get_file(self, repo: str, path: str) -> bytes | None: ...
    def open_pulls(self, repo: str) -> list[PullRequest]: ...
    def open_issues(self, repo: str, label: str) -> list[Issue]: ...
    def create_issue(self, repo: str, title: str, body: str, labels: list[str]) -> int: ...
    def comment(self, repo: str, number: int, body: str) -> None: ...
    def close_issue(self, repo: str, number: int) -> None: ...


class HttpGitHub:
    """GitHub REST over httpx; the token comes from the caller (``GH_TOKEN`` in the CLI)."""

    def __init__(self, token: str, client: httpx.Client | None = None) -> None:
        self.client = client or httpx.Client(base_url=API, timeout=30)
        self.headers = {
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        headers = {**self.headers, **kwargs.pop("headers", {})}
        return self.client.request(method, path, headers=headers, **kwargs)

    def find_repos(self, owner: str, topic: str) -> list[str]:
        """Repos of ``owner`` with ``topic`` that the token can see, private ones included."""
        names: list[str] = []
        for page in range(1, 11):
            response = self._request(
                "GET",
                "/search/repositories",
                params={
                    "q": f"topic:{topic} user:{owner} archived:false",
                    "per_page": 100,
                    "page": page,
                },
            )
            response.raise_for_status()
            items = response.json().get("items", [])
            names += [item["full_name"] for item in items]
            if len(items) < 100:
                break
        return sorted(names)

    def pages_source(self, repo: str) -> str | None:
        """How the repo's Pages site is built (``workflow`` or ``legacy``), or None if off."""
        response = self._request("GET", f"/repos/{repo}/pages")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json().get("build_type")

    def get_file(self, repo: str, path: str) -> bytes | None:
        response = self._request(
            "GET",
            f"/repos/{repo}/contents/{path}",
            headers={"Accept": "application/vnd.github.raw+json"},
        )
        if response.status_code in {403, 404}:
            return None
        response.raise_for_status()
        return response.content

    def _paged(self, path: str, params: dict) -> list[dict]:
        items: list[dict] = []
        for page in range(1, 11):
            response = self._request("GET", path, params={**params, "per_page": 100, "page": page})
            response.raise_for_status()
            batch = response.json()
            items += batch
            if len(batch) < 100:
                break
        return items

    def open_pulls(self, repo: str) -> list[PullRequest]:
        try:
            raw = self._paged(f"/repos/{repo}/pulls", {"state": "open"})
        except httpx.HTTPStatusError:
            return []
        return [
            PullRequest(
                number=item["number"],
                title=item["title"],
                url=item["html_url"],
                created_at=item["created_at"],
                labels=[label["name"] for label in item.get("labels", [])],
            )
            for item in raw
        ]

    def open_issues(self, repo: str, label: str) -> list[Issue]:
        raw = self._paged(f"/repos/{repo}/issues", {"state": "open", "labels": label})
        return [
            Issue(number=i["number"], title=i["title"], body=i.get("body") or "")
            for i in raw
            if "pull_request" not in i
        ]

    def create_issue(self, repo: str, title: str, body: str, labels: list[str]) -> int:
        for label in labels:  # 422 means it exists already
            self._request("POST", f"/repos/{repo}/labels", json={"name": label})
        response = self._request(
            "POST", f"/repos/{repo}/issues", json={"title": title, "body": body, "labels": labels}
        )
        response.raise_for_status()
        return response.json()["number"]

    def comment(self, repo: str, number: int, body: str) -> None:
        self._request(
            "POST", f"/repos/{repo}/issues/{number}/comments", json={"body": body}
        ).raise_for_status()

    def close_issue(self, repo: str, number: int) -> None:
        self._request(
            "PATCH",
            f"/repos/{repo}/issues/{number}",
            json={"state": "closed", "state_reason": "completed"},
        ).raise_for_status()
