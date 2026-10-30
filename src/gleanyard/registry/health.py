"""Recipe health from the projects that use a recipe, read from their public status.json.

The registry never runs a source itself. A project that installed a recipe with `gleanyard
add` names it in each source's ``recipe`` key, and its ``status.json`` reports it. Reading
those reports (one small file per project, no scraping, no model) tells how the recipe fares
across projects. Reports older than ``fresh_days`` and disabled sources do not count. Only
counts are published, never which projects reported them.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import httpx

from gleanyard.registry.model import RecipeHealth, RegistryConfig
from gleanyard.status import ProjectStatus

Fetch = Callable[[str], bytes]
Search = Callable[[str], list[str]]
API = "https://api.github.com"
RAW = "https://raw.githubusercontent.com"
MAX_SEARCH_PAGES = 5


def fetch_url(url: str) -> bytes:
    response = httpx.get(url, timeout=30, follow_redirects=True)
    response.raise_for_status()
    return response.content


def search_topic(topic: str, token: str | None = None) -> list[str]:
    """status.json URLs of the public, unarchived repos with ``topic``."""
    headers = {"X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    urls: list[str] = []
    for page in range(1, MAX_SEARCH_PAGES + 1):
        response = httpx.get(
            f"{API}/search/repositories",
            params={
                "q": f"topic:{topic} is:public archived:false",
                "per_page": 100,
                "page": page,
            },
            headers=headers,
            timeout=30,
        )
        response.raise_for_status()
        items = response.json().get("items", [])
        urls += [
            f"{RAW}/{item['full_name']}/{item.get('default_branch') or 'HEAD'}/status.json"
            for item in items
        ]
        if len(items) < 100:
            break
    return urls


def status_urls(config: RegistryConfig, search: Search | None) -> tuple[list[str], list[str]]:
    """Every status.json to read, and any problem finding them."""
    urls = [
        user if user.startswith(("https://", "http://")) else f"{RAW}/{user}/HEAD/status.json"
        for user in config.users
    ]
    problems: list[str] = []
    if config.discover is not None and search is not None:
        try:
            urls += search(config.discover.topic)
        except httpx.HTTPError as error:
            problems.append(f"could not search for the topic `{config.discover.topic}`: {error}")
    return list(dict.fromkeys(urls)), problems


def read_health(
    config: RegistryConfig,
    recipe_ids: set[str],
    *,
    fetch: Fetch = fetch_url,
    search: Search | None = None,
    now: datetime | None = None,
) -> tuple[dict[str, RecipeHealth], int, list[str]]:
    """Health per recipe id, the number of reports read, and problems (without project names)."""
    now = now or datetime.now(UTC)
    oldest = now - timedelta(days=config.fresh_days)
    health = {recipe_id: RecipeHealth() for recipe_id in recipe_ids}
    urls, problems = status_urls(config, search)
    read = unreadable = 0
    for url in urls:
        try:
            report = ProjectStatus.model_validate_json(fetch(url))
        except (httpx.HTTPError, ValueError):
            unreadable += 1
            continue
        read += 1
        for source in report.sources:
            if source.recipe not in health or not source.enabled or source.health == "disabled":
                continue
            if source.checked_at is None or source.checked_at < oldest:
                continue
            health[source.recipe].add(source.health, source.checked_at, source.last_success_at)
    if unreadable:
        problems.append(f"{unreadable} of {len(urls)} status.json file(s) could not be read")
    return health, read, problems
