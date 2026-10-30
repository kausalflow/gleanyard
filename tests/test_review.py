from datetime import date

import yaml
from test_html import SCHEMA
from typer.testing import CliRunner

from gleanyard.cli import app
from gleanyard.project import load_project
from gleanyard.review import pending_groups, suggest_rights, write_decision
from gleanyard.rights import RightsTier, SourceRights
from gleanyard.schema import ProjectSchema

runner = CliRunner()
PENDING = {
    "review_status": "pending",
    "effective_tier": "link_only",
    "allowed_actions": {"private_store": True, "normalize": True},
    "notes": "See evidence.",
}


def source_text(sid, evidence=None):
    lines = [
        f"id: {sid}",
        "name: Police feed  # shown on the dashboard",
        "schema: event",
        "enabled: false   # the owner turns it on",
        "delivery: rss",
        f"url: https://{sid}.example/feed.rss",
        "extract:",
        "  mode: fields",
        "  map: {title: title, source_url: link, start_date: pubDate}",
    ]
    if evidence:
        lines.append(f"evidence: {evidence}")
    lines += [yaml.safe_dump({"rights": PENDING}, sort_keys=False).rstrip()]
    lines += ["# how long raw pages stay", "retention: {raw_days: 14}"]
    return "\n".join(lines) + "\n"


def make(root, schema=None):
    (root / "sources").mkdir(parents=True)
    (root / "schema.yaml").write_text(yaml.safe_dump(schema or SCHEMA, sort_keys=False))
    for sid in ("a", "b"):
        (root / "sources" / f"{sid}.yaml").write_text(source_text(sid, evidence="shared"))
    (root / "sources" / "c.yaml").write_text(source_text("c"))
    shared = root / "evidence" / "shared"
    shared.mkdir(parents=True)
    (shared / "2026-10-30-terms.yaml").write_text(
        "source: shared\nread_at: 2026-10-30\npages:\n"
        "  - url: https://a.example/terms\n    quote: Free for editorial use.\n"
        "reading: Title and link only.\nsuggested_tier: link_only\n"
    )
    own = root / "evidence" / "c"
    own.mkdir(parents=True)
    (own / "2026-10-30-terms.yaml").write_text(
        "source: c\nread_at: 2026-10-30\npages:\n  - url: https://c.example/imprint\n"
        "reading: A public list.\nsuggested_tier: facts_only\n"
    )
    return root


def test_sources_sharing_evidence_are_one_group(tmp_path):
    project = load_project(make(tmp_path / "p"))
    assert project.ok, project.issues
    groups = pending_groups(project)
    assert [(g.folder, [s.id for s in g.sources]) for g in groups] == [
        ("shared", ["a", "b"]),
        ("c", ["c"]),
    ]
    assert groups[0].suggested_tier == RightsTier.LINK_ONLY
    assert groups[0].reading == "Title and link only."
    assert groups[0].quotes == [("https://a.example/terms", "Free for editorial use.")]


def test_suggestions_follow_tier_and_field_classes():
    schema = ProjectSchema.model_validate(
        SCHEMA
        | {
            "fields": SCHEMA["fields"]
            | {
                "description": {"type": "text", "class": "protected_text"},
                "contact": {"type": "string", "class": "personal"},
            }
        }
    )
    current = SourceRights.model_validate(PENDING)
    link = suggest_rights(RightsTier.LINK_ONLY, schema, current)
    assert link.public_fields == ["title", "start_date", "source_url"]
    assert link.allowed_actions.redistribute_facts and not link.allowed_actions.redistribute_text
    facts = suggest_rights(RightsTier.FACTS_ONLY, schema, current)
    assert facts.public_fields == ["title", "start_date", "time_text", "venue", "source_url"]
    licensed = suggest_rights(RightsTier.LICENSED, schema, current)
    assert "description" in licensed.public_fields and "contact" not in licensed.public_fields
    assert licensed.allowed_actions.redistribute_text
    internal = suggest_rights(RightsTier.INTERNAL_ONLY, schema, current)
    assert internal.public_fields == [] and not internal.allowed_actions.redistribute_facts
    blocked = suggest_rights(RightsTier.BLOCKED, schema, current)
    assert not blocked.allowed_actions.private_store


def test_write_keeps_the_rest_of_the_file(tmp_path):
    path = tmp_path / "a.yaml"
    path.write_text(source_text("a"))
    rights = SourceRights.model_validate(
        PENDING
        | {
            "review_status": "approved",
            "reviewed_at": date(2026, 10, 30),
            "public_fields": ["title"],
        }
    )
    write_decision(path, rights, enabled=True)
    text = path.read_text()
    assert "enabled: true   # the owner turns it on" in text
    assert "name: Police feed  # shown on the dashboard" in text
    assert "# how long raw pages stay\nretention: {raw_days: 14}\n" in text
    assert "reviewed_at: 2026-10-30\n" in text
    data = yaml.safe_load(text)
    assert data["rights"]["review_status"] == "approved"
    assert data["rights"]["public_fields"] == ["title"]


def test_review_asks_once_per_shared_evidence_and_writes_the_answers(tmp_path):
    root = make(tmp_path / "p")
    # Group 1 (a, b): accept link_only and its suggestions, approve, enable, keep notes.
    # Group 2 (c): facts_only, change the public fields, reject.
    answers = "\n".join(
        ["", "", "", "", "", "facts_only", "n", "", "title, source_url", "reject", "Gone"]
    )
    result = runner.invoke(app, ["rights", "review", str(root)], input=answers + "\n")
    assert result.exit_code == 0, result.output
    assert "Title and link only." in result.output
    assert "Free for editorial use." in result.output
    a, b, c = (yaml.safe_load((root / "sources" / f"{s}.yaml").read_text()) for s in "abc")
    assert a["rights"] == b["rights"]
    assert a["enabled"] is True and b["enabled"] is True
    assert a["rights"]["review_status"] == "approved"
    assert a["rights"]["effective_tier"] == "link_only"
    assert a["rights"]["public_fields"] == ["title", "start_date", "source_url"]
    assert a["rights"]["allowed_actions"] == {
        "discover_urls": True,
        "private_store": True,
        "normalize": True,
        "redistribute_facts": True,
    }
    assert a["rights"]["notes"] == "See evidence."
    assert c["enabled"] is False
    assert c["rights"]["review_status"] == "rejected"
    assert c["rights"]["public_fields"] == ["title", "source_url"]
    assert c["rights"]["notes"] == "Gone"
    assert load_project(root).ok
    again = runner.invoke(app, ["rights", "review", str(root)])
    assert "no sources wait for a rights review" in again.output


def test_skip_writes_nothing(tmp_path):
    root = make(tmp_path / "p")
    before = (root / "sources" / "c.yaml").read_text()
    result = runner.invoke(app, ["rights", "review", str(root), "-s", "c"], input="\n\nskip\n")
    assert result.exit_code == 0, result.output
    assert (root / "sources" / "c.yaml").read_text() == before
