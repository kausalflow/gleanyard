import pytest
from test_html import TODAY, make_project, site

from gleanyard.errors import BoundsExceeded, ModelError
from gleanyard.extract.llm import Completion, PydanticAiClient, field_guide, page_text
from gleanyard.project import load_project
from gleanyard.run import collect_source, load_records

PAGE = b"""<html><head><style>.x{}</style><script>var a=1;</script></head><body>
<h1>Hof events</h1>
<p>Apple day on 15 October 2026 at 11 Uhr. <a href="/events/apple">Details</a></p>
<p>Bread baking, 12 November 2026.</p></body></html>"""

ITEMS = {
    "items": [
        {
            "title": "Apple day",
            "start_date": "2026-10-15",
            "time_text": "11 Uhr",
            "source_url": "/events/apple",
        },
        {"title": "Bread baking", "start_date": "2026-11-12", "source_url": "/events/bread"},
        {"title": "Not a date", "start_date": "someday", "source_url": "/x", "venue": None},
    ]
}


class FakeClient:
    def __init__(self, data=ITEMS, fail=False):
        self.data, self.fail, self.prompts = data, fail, []

    def complete(self, *, model, system, prompt):
        self.prompts.append(prompt)
        if self.fail:
            raise ConnectionError("provider down")
        return Completion(data=self.data, input_tokens=2000, output_tokens=200)


def llm_source(**llm):
    return {
        "delivery": "html",
        "url": "https://hof.example/events",
        "extract": {
            "mode": "llm",
            "llm": {"model": "test:model", **llm},
            "defaults": {"venue": "Synthetic Hof"},
        },
    }


def project_for(tmp_path, allow=True, **llm):
    source = llm_source(**llm)
    root = make_project(tmp_path / "p", source)
    if allow:
        import yaml

        path = root / "sources" / "s.yaml"
        document = yaml.safe_load(path.read_text())
        document["rights"]["allowed_actions"]["send_to_model"] = True
        path.write_text(yaml.safe_dump(document))
    return load_project(root)


def collect(project, client, **kwargs):
    return collect_source(
        project,
        project.sources[0],
        client=site({"/events": PAGE}),
        llm_client=client,
        today=TODAY,
        **kwargs,
    )


def test_page_text_keeps_links_and_drops_scripts():
    text = page_text(PAGE, "s", 10_000)
    assert "Apple day on 15 October 2026" in text and "</events/apple>" in text
    assert "var a" not in text


def test_field_guide_lists_requirements(tmp_path):
    project = project_for(tmp_path)
    guide = field_guide(project.schema)
    assert "- title (string, required)" in guide and "- venue (string, optional)" in guide


def test_llm_extraction_goes_through_schema_validation(tmp_path):
    project = project_for(tmp_path)
    client = FakeClient()
    run = collect(project, client)
    assert run.ok and run.records == 2 and run.rejections == 1
    assert run.model_calls == 1 and 0 < run.cost_eur < 0.5
    record = load_records(project, "s")[0]
    assert record.provenance.mode == "llm"
    assert record.values["venue"] == "Synthetic Hof"
    assert record.values["source_url"] == "https://hof.example/events/apple"
    assert "Apple day on 15 October 2026" in client.prompts[0]


def test_unchanged_page_is_not_sent_again(tmp_path):
    project = project_for(tmp_path)
    first, second = FakeClient(), FakeClient()
    collect(project, first)
    again = collect(project, second)
    assert again.ok and again.records == 2 and again.model_calls == 0
    assert second.prompts == []


def test_changing_instructions_asks_the_model_again(tmp_path):
    project = project_for(tmp_path)
    collect(project, FakeClient())
    source = project.sources[0]
    extract = source.extract.model_copy(update={"instructions": "Only workshops."})
    changed = source.model_copy(update={"extract": extract})
    client = FakeClient()
    collect_source(project, changed, client=site({"/events": PAGE}), llm_client=client, today=TODAY)
    assert len(client.prompts) == 1 and "Only workshops." in client.prompts[0]


def test_only_if_changed_false_always_asks(tmp_path):
    project = project_for(tmp_path, only_if_changed=False)
    collect(project, FakeClient())
    client = FakeClient()
    collect(project, client)
    assert len(client.prompts) == 1


def test_from_cache_uses_stored_output_and_never_calls_a_model(tmp_path):
    project = project_for(tmp_path)
    collect(project, FakeClient())
    run = collect_source(project, project.sources[0], from_cache=True, today=TODAY)
    assert run.ok and run.records == 2 and run.model_calls == 0


def test_page_cap_stops_a_run(tmp_path):
    project = project_for(tmp_path, max_pages_per_run=1)
    source = project.sources[0]
    # a second call in the same session exceeds the cap
    from gleanyard.extract.llm import LlmSession
    from gleanyard.run import _store

    session = LlmSession(store=_store(project), client=FakeClient())
    response = site({"/events": PAGE}).get("https://hof.example/events")

    class R:
        body = response.content

    session.items(source, project.schema, R())
    other = b"<html><body>Different page</body></html>"
    R.body = other
    with pytest.raises(BoundsExceeded, match="max_pages_per_run"):
        session.items(source, project.schema, R())


def test_cost_cap_stops_before_calling(tmp_path):
    project = project_for(tmp_path, max_cost_per_run_eur=0.0001)
    client = FakeClient()
    run = collect(project, client)
    assert not run.ok and run.error_kind == "bounds_exceeded"
    assert client.prompts == []


def test_provider_failure_and_bad_output_are_model_errors(tmp_path):
    project = project_for(tmp_path)
    failed = collect(project, FakeClient(fail=True))
    assert failed.error_kind == "model" and "provider down" in failed.error
    bad = collect(project, FakeClient(data={"records": []}))
    assert bad.error_kind == "model"


def test_llm_needs_send_to_model_right(tmp_path):
    project = project_for(tmp_path, allow=False)
    assert any("send_to_model" in issue.message for issue in project.issues)
    run = collect(project, FakeClient())
    assert run.error_kind == "rights"


def test_pydantic_ai_client_with_the_test_model():
    pytest.importorskip("pydantic_ai")
    completion = PydanticAiClient().complete(model="test", system="s", prompt="p")
    assert isinstance(completion.data["items"], list)
    assert completion.input_tokens > 0


def test_model_error_without_extra_is_clear():
    assert ModelError("s", "x").kind.value == "model"
