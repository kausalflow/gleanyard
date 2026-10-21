from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
from test_html import make_project
from typer.testing import CliRunner

from gleanyard.cli import app
from gleanyard.fetch import Fetcher
from gleanyard.project import load_project
from gleanyard.status import load_status
from gleanyard.terms import ABSENT, check_terms, fingerprint, latest_evidence

runner = CliRunner()
NOW = datetime(2026, 10, 21, 6, tzinfo=UTC)
TERMS = (
    b"<html><body><nav>Menu</nav><main><h1>Impressum</h1>"
    b"<p>Inhalte sind geschuetzt.</p></main><script>var t=1</script></body></html>"
)


def write_project(root, review="2026-10-10"):
    make_project(
        root,
        {
            "delivery": "json",
            "url": "https://site.example/api",
            "extract": {
                "mode": "fields",
                "map": {"title": "name", "start_date": "date", "source_url": "url"},
            },
        },
    )
    write_evidence(root, review)
    return root


def write_evidence(root, review):
    directory = root / "evidence" / "s"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{review}-terms.yaml").write_text(
        f"source: s\nread_at: {review}\npages:\n"
        "  - url: https://site.example/impressum\n    quote: geschuetzt\n"
    )


def site(pages, calls=None):
    def handler(request):
        if calls is not None:
            calls.append(request.url.path)
        body = pages.get(request.url.path)
        if body is None:
            return httpx.Response(404)
        return httpx.Response(200, content=body, headers={"content-type": "text/html"})

    return httpx.Client(transport=httpx.MockTransport(handler))


def run(project, pages, previous=None, now=NOW, **kwargs):
    return check_terms(
        project, project.sources, previous or {}, client=site(pages), now=now, **kwargs
    )[0]


@pytest.fixture
def project(tmp_path):
    return load_project(write_project(tmp_path / "p"))


def test_evidence_lists_the_pages_and_robots(project):
    evidence = latest_evidence(project, project.sources[0])
    assert evidence.reviewed_on == date(2026, 10, 10)
    assert evidence.urls == [
        "https://site.example/impressum",
        "https://site.example/robots.txt",
    ]


def test_fingerprint_ignores_markup_scripts_and_navigation():
    noisy = (
        TERMS.replace(b"var t=1", b"var t=2")
        .replace(b"Menu", b"Home")
        .replace(b"<p>", b"<p class='x'>  ")
    )
    assert fingerprint(TERMS, "text/html") == fingerprint(noisy, "text/html")
    assert fingerprint(TERMS, "text/html") != fingerprint(
        TERMS.replace(b"geschuetzt", b"frei"), "text/html"
    )


def test_first_check_is_the_baseline_and_a_change_stays_until_a_new_review(tmp_path):
    root = write_project(tmp_path / "p")
    project = load_project(root)
    first = run(project, {"/impressum": TERMS})
    assert first.checked and first.state.changed == []
    assert first.state.pages["https://site.example/robots.txt"] == ABSENT

    later = NOW + timedelta(days=7)
    edited = {"/impressum": TERMS.replace(b"geschuetzt", b"frei nutzbar")}
    second = run(project, edited, {"s": first.state}, now=later)
    assert second.state.changed == ["https://site.example/impressum"]
    assert second.state.changed_at == later

    # Reverting the page does not clear it: someone has to read the terms again.
    third = run(project, {"/impressum": TERMS}, {"s": second.state}, now=later + timedelta(days=7))
    assert third.state.changed == ["https://site.example/impressum"]

    write_evidence(root, "2026-11-04")
    project = load_project(root)
    reviewed = run(project, edited, {"s": third.state}, now=later + timedelta(days=8))
    assert reviewed.state.changed == [] and reviewed.state.reviewed_on == date(2026, 11, 4)


def test_a_new_robots_txt_is_a_change(project):
    first = run(project, {"/impressum": TERMS})
    later = run(
        project,
        {"/impressum": TERMS, "/robots.txt": b"User-agent: *\nDisallow: /api"},
        {"s": first.state},
        now=NOW + timedelta(days=7),
    )
    assert later.state.changed == ["https://site.example/robots.txt"]


def test_checks_wait_a_week_unless_forced(project):
    first = run(project, {"/impressum": TERMS})
    soon = run(project, {}, {"s": first.state}, now=NOW + timedelta(days=1))
    assert not soon.checked and soon.state == first.state
    forced = run(project, {"/impressum": TERMS}, {"s": first.state}, now=NOW, force=True)
    assert forced.checked


def test_unreadable_pages_are_errors_not_changes(project):
    first = run(project, {"/impressum": TERMS})

    def handler(request):
        return httpx.Response(404) if request.url.path == "/robots.txt" else httpx.Response(500)

    later = check_terms(
        project,
        project.sources,
        {"s": first.state},
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        now=NOW + timedelta(days=7),
    )[0]
    assert later.state.changed == []
    assert "https://site.example/impressum" in later.state.errors


def test_a_source_without_evidence_is_noted(tmp_path):
    root = write_project(tmp_path / "p")
    for path in (root / "evidence" / "s").iterdir():
        path.unlink()
    result = run(load_project(root), {})
    assert not result.checked and "no evidence" in result.note


def test_cli_writes_status_and_exits_1_on_a_change(tmp_path, monkeypatch):
    root = write_project(tmp_path / "p")
    pages = {"/impressum": TERMS}

    def fetcher(*args, **kwargs):
        return Fetcher(*args, **{**kwargs, "client": site(pages)})

    monkeypatch.setattr("gleanyard.terms.Fetcher", fetcher)
    assert runner.invoke(app, ["terms", str(root)]).exit_code == 0
    status = load_status(load_project(root))
    assert status.sources[0].terms.reviewed_on == date(2026, 10, 10)
    pages["/impressum"] = TERMS.replace(b"geschuetzt", b"frei")
    result = runner.invoke(app, ["terms", str(root), "--force"])
    assert result.exit_code == 1 and "CHANGED" in result.stdout
