"""Which problems are active now, and keeping one GitHub issue per active problem.

Alerts are stateless: each problem has a stable key. A key that is active and has no open
issue opens one; a key that is active and already has an issue changes nothing (so a source
that stays down does not nag); an issue whose key is no longer active is closed with a note.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from pydantic import BaseModel

from gleanyard.hub.config import HubConfig
from gleanyard.hub.github import GitHub
from gleanyard.hub.state import HubState, RepoState
from gleanyard.status import SourceStatus

LABEL = "hub-alert"
MARKER = "<!-- gleanyard-hub:{key} -->"


class Alert(BaseModel):
    key: str
    kind: str
    title: str
    body: str


def compute_alerts(state: HubState, config: HubConfig, now: datetime | None = None) -> list[Alert]:
    now = now or state.generated_at
    alerts: list[Alert] = []
    if state.problems:
        alerts.append(
            Alert(
                key="hub:problem",
                kind="hub_problem",
                title="The hub could not find every project",
                body="\n".join(f"- {problem}" for problem in state.problems),
            )
        )
    for repo in state.repos:
        alerts += _repo_alerts(repo, config, now)
    return alerts


def _repo_alerts(repo: RepoState, config: HubConfig, now: datetime) -> list[Alert]:
    name = repo.repo
    alerts: list[Alert] = []
    if repo.status is None:
        alerts.append(
            Alert(
                key=f"{name}:no-status",
                kind="no_status",
                title=f"{name}: no status report",
                body=f"The hub could not read the report of `{name}`: {repo.problem}.",
            )
        )
    else:
        age = now - repo.status.generated_at
        if age > timedelta(hours=config.missed_run_hours):
            alerts.append(
                Alert(
                    key=f"{name}:missed-run",
                    kind="missed_run",
                    title=f"{name}: scheduled run missed",
                    body=(
                        f"The last report of `{name}` is from {repo.status.generated_at:%F %H:%M} "
                        f"UTC, {age.total_seconds() / 3600:.0f} hours ago (limit "
                        f"{config.missed_run_hours}). Check that the `collect` workflow is "
                        "enabled: GitHub pauses scheduled workflows after 60 days without commits."
                    ),
                )
            )
        for source in repo.status.sources:
            if source.health == "failing":
                alerts.append(
                    Alert(
                        key=f"{name}:{source.id}:failing",
                        kind="source_failing",
                        title=f"{name}: {source.id} is failing",
                        body=(
                            f"`{source.id}` failed {source.consecutive_failures} run(s) in a "
                            f"row: `{source.error_kind}` {source.error or ''}\n\n"
                            f"Last success: {source.last_success_at or 'never'}."
                        ),
                    )
                )
            elif source.health == "degraded":
                alerts += _degraded_alerts(name, source)
            if source.terms is not None and source.terms.changed:
                alerts.append(_terms_alert(name, source))
    for pull in repo.pulls:
        waited = now - pull.created_at
        if waited > timedelta(days=config.pr_waiting_days):
            alerts.append(
                Alert(
                    key=f"{name}#{pull.number}:waiting",
                    kind="pr_waiting",
                    title=f"{name}: PR #{pull.number} has waited {waited.days} days",
                    body=f"[{pull.title}]({pull.url}) is open and labelled for setup or repair.",
                )
            )
    return alerts


DEGRADED_TITLES = {
    "record_drop": "lost records",
    "fill_rate_drop": "fields stopped filling",
    "rejection_spike": "more records rejected",
    "unchanged": "data stopped changing",
}


def _degraded_alerts(name: str, source: SourceStatus) -> list[Alert]:
    """One alert per kind of warning, so a fill-rate drop and a count drop are separate issues."""
    if not source.warnings:
        # A report from before per-check warnings: a degraded source meant fewer records.
        return [
            Alert(
                key=f"{name}:{source.id}:degraded",
                kind="count_drop",
                title=f"{name}: {source.id} lost records",
                body=f"`{source.id}`: {source.note or 'record count dropped'}.",
            )
        ]
    kinds: dict[str, list[str]] = {}
    for check in source.warnings:
        kinds.setdefault(check.kind, []).append(check.message)
    return [
        Alert(
            key=f"{name}:{source.id}:{kind}",
            kind=kind,
            title=f"{name}: {source.id} {DEGRADED_TITLES.get(kind, kind)}",
            body=f"`{source.id}` ran without errors, but:\n\n"
            + "\n".join(f"- {message}" for message in messages)
            + "\n\nCompare with the last good run: `gleanyard collect --from-cache -s "
            + f"{source.id}`, then follow the Repair procedure in `CLAUDE.md`.",
        )
        for kind, messages in kinds.items()
    ]


def _terms_alert(name: str, source: SourceStatus) -> Alert:
    assert source.terms is not None
    since = f"{source.terms.changed_at:%F}" if source.terms.changed_at else "the last check"
    return Alert(
        key=f"{name}:{source.id}:terms_changed",
        kind="terms_changed",
        title=f"{name}: {source.id} terms changed",
        body=(
            f"The text of these pages differs from the review of {source.terms.reviewed_on} "
            f"(noticed {since}):\n\n"
            + "\n".join(f"- {url}" for url in source.terms.changed)
            + "\n\nRead them again and add a new `evidence/"
            + f"{source.id}/<date>-terms.yaml`; adjust `rights` if the terms now say otherwise. "
            "The issue closes after the next `gleanyard terms` run."
        ),
    )


class SyncResult(BaseModel):
    opened: list[str] = []
    closed: list[str] = []
    unchanged: int = 0


def sync_issues(
    github: GitHub, hub_repo: str, alerts: list[Alert], dry_run: bool = False
) -> SyncResult:
    result = SyncResult()
    open_issues = {}
    for issue in github.open_issues(hub_repo, LABEL):
        key = _key_of(issue.body)
        if key:
            open_issues[key] = issue
    active = {alert.key: alert for alert in alerts}
    for key, alert in active.items():
        if key in open_issues:
            result.unchanged += 1
            continue
        result.opened.append(key)
        if not dry_run:
            body = f"{MARKER.format(key=key)}\n{alert.body}"
            github.create_issue(hub_repo, alert.title, body, [LABEL, alert.kind])
    for key, issue in open_issues.items():
        if key in active or key.startswith("digest:"):
            continue
        result.closed.append(key)
        if not dry_run:
            github.comment(hub_repo, issue.number, "No longer active, closing.")
            github.close_issue(hub_repo, issue.number)
    return result


def _key_of(body: str) -> str | None:
    start = body.find("<!-- gleanyard-hub:")
    if start < 0:
        return None
    end = body.find(" -->", start)
    return body[start + len("<!-- gleanyard-hub:") : end] if end > 0 else None


def weekly_digest(
    github: GitHub, hub_repo: str, state: HubState, config: HubConfig, dry_run: bool = False
) -> str | None:
    """On the digest weekday, post one issue per ISO week, even when everything is green."""
    now = state.generated_at.astimezone(UTC)
    if config.digest_weekday is None or now.weekday() != config.digest_weekday:
        return None
    year, week, _ = now.isocalendar()
    key = f"digest:{year}-W{week:02d}"
    existing = {_key_of(issue.body): issue for issue in github.open_issues(hub_repo, LABEL)}
    if key in existing:
        return None
    lines = [
        f"Weekly digest for {now:%F}.",
        "",
        "| Repo | State | Sources | Records |",
        "| --- | --- | --- | --- |",
    ]
    for repo in state.repos:
        sources = repo.status.sources if repo.status else []
        lines.append(
            f"| {repo.repo} | {repo.level} | {len(sources)} | {sum(s.records for s in sources)} |"
        )
    if not dry_run:
        for old_key, issue in existing.items():
            if old_key and old_key.startswith("digest:"):
                github.close_issue(hub_repo, issue.number)
        body = f"{MARKER.format(key=key)}\n" + "\n".join(lines)
        github.create_issue(hub_repo, f"Weekly digest {year}-W{week:02d}", body, [LABEL, "digest"])
    return key
