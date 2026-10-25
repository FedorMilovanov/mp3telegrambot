"""Executed Gemini, publication, and audit pipelines for expanded Telegraph pages."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from services import telegraph_pages as pages


def _run(callback, *args, **kwargs):
    return asyncio.run(callback(*args, **kwargs))


async def _observe(**_kwargs):
    return None


def _silence_logger(monkeypatch):
    def noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        pages,
        "logger",
        SimpleNamespace(info=noop, warning=noop, error=noop, exception=noop),
    )


def _gemini_client(responder):
    class Models:
        async def generate_content(self, **kwargs):
            return responder(kwargs)

    return SimpleNamespace(aio=SimpleNamespace(models=Models()))


def _prepare_gemini(monkeypatch, responder):
    client = _gemini_client(responder)
    monkeypatch.setattr(pages, "GEMINI_CLIENTS", [client])
    monkeypatch.setattr(
        pages,
        "compact_prompt_for_generation",
        lambda _prompt: SimpleNamespace(
            saved_chars=1,
            original_chars=10,
            compacted_chars=9,
            removed_lines=1,
            text="compacted",
        ),
    )
    monkeypatch.setattr(pages, "make_text_config_smart", lambda **kwargs: kwargs)
    monkeypatch.setattr(pages, "alog_gemini_response", _observe)
    monkeypatch.setattr(pages, "alog_gemini_run", _observe)

    async def generate(clients, operation, **_kwargs):
        return await operation(clients[0])

    monkeypatch.setattr(pages, "gemini_generate", generate)
    return client


def test_gemini_request_without_clients_returns_none(monkeypatch):
    monkeypatch.setattr(pages, "GEMINI_CLIENTS", [])
    assert _run(pages._gemini_text_request, "prompt") is None


def test_gemini_request_recovers_candidate_parts_and_logs_usage(monkeypatch):
    class Response:
        usage_metadata = SimpleNamespace(prompt_token_count=1, thoughts_token_count=2, candidates_token_count=3)
        candidates = [
            SimpleNamespace(
                finish_reason="STOP",
                content=SimpleNamespace(
                    parts=[
                        SimpleNamespace(thought=True, text="hidden"),
                        SimpleNamespace(thought=False, text="visible"),
                    ]
                ),
            )
        ]

        @property
        def text(self):
            raise ValueError("no quick text")

    _prepare_gemini(monkeypatch, lambda _kwargs: Response())
    result = _run(
        pages._gemini_text_request,
        "prompt",
        response_schema={"type": "object"},
        allow_model_fallback=True,
    )
    assert result == "visible"


def test_gemini_request_reports_max_tokens_and_empty_response(monkeypatch):
    response = SimpleNamespace(
        text="visible",
        candidates=[SimpleNamespace(finish_reason="MAX_TOKENS")],
        usage_metadata=SimpleNamespace(prompt_token_count=1, thoughts_token_count=2, candidates_token_count=3),
    )
    _prepare_gemini(monkeypatch, lambda _kwargs: response)
    assert _run(pages._gemini_text_request, "prompt") == "visible"

    empty = SimpleNamespace(text="", candidates=[], usage_metadata=None)
    _prepare_gemini(monkeypatch, lambda _kwargs: empty)
    assert _run(pages._gemini_text_request, "prompt") is None


def test_gemini_request_enforces_initial_time_budget(monkeypatch):
    _prepare_gemini(monkeypatch, lambda _kwargs: SimpleNamespace(text="too late"))
    _silence_logger(monkeypatch)
    times = iter([0, 181, 182, 183])
    monkeypatch.setattr(pages.time, "time", lambda: next(times))
    assert _run(pages._gemini_text_request, "prompt") is None


def test_gemini_request_enforces_budget_after_schema_error(monkeypatch):
    def fail(_kwargs):
        raise RuntimeError("schema unsupported")

    _prepare_gemini(monkeypatch, fail)
    monkeypatch.setattr(pages, "is_quota_error", lambda _error: False)
    monkeypatch.setattr(pages, "is_overload_error", lambda _error: False)
    monkeypatch.setattr(pages.capacity_control, "is_timeout_error", lambda _error: False)
    _silence_logger(monkeypatch)
    times = iter([0, 1, 181, 182, 183])
    monkeypatch.setattr(pages.time, "time", lambda: next(times))
    assert (
        _run(
            pages._gemini_text_request,
            "prompt",
            response_schema={"type": "object"},
        )
        is None
    )


def _prepare_publisher(monkeypatch, create_responder, edit_responder=None):
    monkeypatch.setattr(
        pages,
        "_section_to_nodes_v2",
        lambda section, **_kwargs: [{"tag": "p", "children": [section.get("title", "")]}],
    )
    monkeypatch.setattr(pages, "_build_toc_nodes_v2", lambda *_a, **_k: [{"tag": "p", "children": ["TOC"]}])
    monkeypatch.setattr(pages, "_build_nav_nodes_v2", lambda *_a, **_k: [{"tag": "p", "children": ["NAV"]}])
    creates = []

    async def create(title, author, nodes, _loop):
        creates.append((title, author, nodes))
        return create_responder(len(creates), nodes)

    edits = []

    async def edit(url, title, author, nodes, _loop):
        edits.append((url, title, author, nodes))
        if edit_responder is None:
            return True
        return edit_responder(len(edits), nodes)

    sleeps = []

    async def sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(pages, "_create_telegraph_page_single", create)
    monkeypatch.setattr(pages, "_edit_telegraph_page", edit)
    monkeypatch.setattr(pages.asyncio, "sleep", sleep)
    return creates, edits, sleeps


def test_publish_expanded_empty_and_simple_related_page(monkeypatch):
    creates, _edits, _sleeps = _prepare_publisher(monkeypatch, lambda _count, _nodes: (None, "FATAL"))
    assert _run(pages._publish_expanded_page, [], [], "Page", "Raw", "Author") is None
    assert creates == []

    creates, edits, sleeps = _prepare_publisher(monkeypatch, lambda _count, _nodes: ("https://telegra.ph/page", None))
    monkeypatch.setattr(pages, "extract_scripture_refs", lambda _ai: ["John 3:16"])

    async def related(**_kwargs):
        return [{"title": "Related"}]

    monkeypatch.setattr(pages, "aget_related_materials", related)
    monkeypatch.setattr(
        pages, "_build_related_materials_nodes", lambda value: [{"tag": "p", "children": [value[0]["title"]]}]
    )
    result = _run(
        pages._publish_expanded_page,
        [{"title": "A", "content": "Text"}],
        [{"title": "A", "time": "1:00"}],
        "Page",
        "Raw",
        "Author",
        ai_data={"real_title": "Source"},
        video_id="video",
    )
    assert result == "https://telegra.ph/page"
    assert sleeps == [2]
    assert edits[0][3][0]["children"] == ["TOC"]
    assert edits[0][3][-1]["children"] == ["Related"]


def test_publish_expanded_recursively_splits_multiple_sections(monkeypatch):
    def create(count, _nodes):
        if count == 1:
            return None, "CONTENT_TOO_BIG"
        return f"https://telegra.ph/part-{count - 1}", None

    creates, edits, _sleeps = _prepare_publisher(monkeypatch, create)
    result = _run(
        pages._publish_expanded_page,
        [{"title": "A", "content": "Text"}, {"title": "B", "content": "Text"}],
        [{"title": "A"}, {"title": "B"}],
        "Page",
        "Raw",
        "Author",
    )
    assert result == "https://telegra.ph/part-1"
    assert len(creates) == 3
    assert [item[1] for item in edits] == [
        "Page (часть 1/2)",
        "Page (часть 2/2)",
    ]
    assert edits[1][3][0]["children"] == ["NAV"]


def test_publish_expanded_auto_splits_one_long_section(monkeypatch):
    def create(count, _nodes):
        if count == 1:
            return None, "CONTENT_TOO_BIG"
        return f"https://telegra.ph/part-{count - 1}", None

    creates, _edits, _sleeps = _prepare_publisher(monkeypatch, create)
    section = {
        "title": "Section",
        "content": "\n\n".join(f"Paragraph {index}" for index in range(6)),
    }
    result = _run(pages._publish_expanded_page, [section], [], "Page", "Raw", "Author")
    assert result == "https://telegra.ph/part-1"
    assert len(creates) == 3
    body_titles = [call[2][0]["children"][0] for call in creates]
    assert body_titles[1:] == ["Section (ч.\u200b1)", "Section (ч.\u200b2)"]


@pytest.mark.parametrize("error", ["CONTENT_TOO_BIG", "FATAL"])
def test_publish_expanded_unsplittable_or_fatal_create_returns_none(monkeypatch, error):
    _prepare_publisher(monkeypatch, lambda _count, _nodes: (None, error))
    result = _run(
        pages._publish_expanded_page,
        [{"title": "A", "content": "One paragraph"}],
        [],
        "Page",
        "Raw",
        "Author",
    )
    assert result is None


def test_publish_expanded_retries_edit_then_drops_outline(monkeypatch):
    _creates, edits, sleeps = _prepare_publisher(
        monkeypatch,
        lambda _count, _nodes: ("https://telegra.ph/page", None),
        lambda count, _nodes: count >= 4,
    )
    result = _run(
        pages._publish_expanded_page,
        [{"title": "A", "content": "Text"}],
        [{"title": "A"}],
        "Page",
        "Raw",
        "Author",
        include_toc=True,
    )
    assert result == "https://telegra.ph/page"
    assert len(edits) == 4
    assert edits[0][3][0]["children"] == ["TOC"]
    assert edits[-1][3][0]["children"] != ["TOC"]
    assert sleeps == [2, 3, 6, 12, 2]


def test_publish_expanded_preserves_created_page_after_final_edit_failure(monkeypatch):
    _creates, edits, sleeps = _prepare_publisher(
        monkeypatch,
        lambda _count, _nodes: ("https://telegra.ph/page", None),
        lambda _count, _nodes: False,
    )
    result = _run(
        pages._publish_expanded_page,
        [{"title": "A", "content": "Text"}],
        [],
        "Page",
        "Raw",
        "Author",
        include_toc=False,
    )
    assert result == "https://telegra.ph/page"
    assert len(edits) == 3
    assert sleeps == [2, 3, 6, 12]


def test_content_audit_flags_and_warning_count(monkeypatch):
    monkeypatch.delenv("EXPANDED_CONTENT_AUDIT_RETRY", raising=False)
    assert pages._content_audit_retry_enabled() is True
    monkeypatch.setenv("EXPANDED_CONTENT_AUDIT_RETRY", "off")
    assert pages._content_audit_retry_enabled() is False

    def has_warnings(issues):
        if issues == ["explode"]:
            raise RuntimeError("broken issue")
        return issues != ["ok"]

    monkeypatch.setattr(pages, "has_content_audit_warnings", has_warnings)
    assert pages._audit_warning_count(["bad", "ok"]) == 1
    assert pages._audit_warning_count(["explode", "ok"]) == 2


def _audit_kwargs(**overrides):
    kwargs = {
        "label": "StudyAnalysis",
        "original_prompt": "original task",
        "outline": [],
        "sections": [{"title": "A", "content": "Text"}],
        "issues": ["issue"],
        "max_tokens": 8000,
        "thinking_level": "high",
        "response_schema": {"type": "object"},
        "expected_author": "Author",
    }
    kwargs.update(overrides)
    return kwargs


def _prepare_audit_retry(monkeypatch, raw):
    monkeypatch.setenv("EXPANDED_CONTENT_AUDIT_RETRY", "1")
    monkeypatch.setattr(pages, "has_content_audit_warnings", lambda issues: bool(issues))
    monkeypatch.setattr(pages, "format_content_audit_issues", lambda *_a, **_k: "summary")

    async def generate(*_args, **_kwargs):
        return raw

    monkeypatch.setattr(pages, "_gemini_text_request", generate)


def test_content_audit_retry_honors_opt_out(monkeypatch):
    monkeypatch.setenv("EXPANDED_CONTENT_AUDIT_RETRY", "0")
    assert _run(pages._retry_expanded_sections_for_content_audit, **_audit_kwargs()) is None


def test_content_audit_retry_handles_empty_broken_and_empty_sections(monkeypatch):
    _prepare_audit_retry(monkeypatch, "")
    kwargs = _audit_kwargs(sections=[{"title": "A", "content": "x" * 76_000}])
    assert _run(pages._retry_expanded_sections_for_content_audit, **kwargs) is None

    _prepare_audit_retry(monkeypatch, "broken")
    assert _run(pages._retry_expanded_sections_for_content_audit, **_audit_kwargs()) is None

    _prepare_audit_retry(monkeypatch, json.dumps({"outline": [], "sections": [{}]}))
    assert _run(pages._retry_expanded_sections_for_content_audit, **_audit_kwargs()) is None


@pytest.mark.parametrize(
    ("label", "before", "after", "accepted"),
    [
        ("StudyAnalysis", ["a", "b"], ["a"], True),
        ("ReflectionApplication", ["a"], ["a"], True),
        ("StudyAnalysis", ["a"], ["a", "b"], False),
    ],
)
def test_content_audit_retry_accepts_only_quality_improvement(monkeypatch, label, before, after, accepted):
    raw = json.dumps({"outline": [], "sections": [{"title": "A", "content": "Text"}]})
    _prepare_audit_retry(monkeypatch, raw)
    monkeypatch.setattr(pages, "_audit_warning_count", lambda issues: len(issues))
    monkeypatch.setattr(
        pages,
        "audit_expanded_sections",
        lambda sections, outline, **_kwargs: (sections, outline, after),
    )
    result = _run(
        pages._retry_expanded_sections_for_content_audit,
        **_audit_kwargs(label=label, issues=before),
    )
    assert (result is not None) is accepted
    if accepted:
        assert result[2] == after


def _prepare_run(monkeypatch, responses):
    responses = iter(responses)
    calls = []

    async def generate(*args, **kwargs):
        calls.append((args, kwargs))
        return next(responses)

    monkeypatch.setattr(pages, "_gemini_text_request", generate)
    monkeypatch.setattr(
        pages,
        "get_adaptive_text_generation_params",
        lambda *_args: SimpleNamespace(reason="adaptive", temperature=0.2, max_tokens=1234),
    )
    monkeypatch.setattr(pages, "_expanded_structured_output_enabled", lambda: True)
    monkeypatch.setattr(pages, "expanded_page_response_schema", lambda: {"type": "object"})
    monkeypatch.setattr(
        pages,
        "audit_expanded_sections",
        lambda sections, outline, **_kwargs: (sections, outline, []),
    )
    monkeypatch.setattr(pages, "get_content_audit_mode", lambda: "warn")
    monkeypatch.setattr(pages, "has_content_audit_warnings", lambda issues: bool(issues))
    monkeypatch.setattr(pages, "format_content_audit_issues", lambda *_a, **_k: "summary")

    async def no_retry(**_kwargs):
        return None

    monkeypatch.setattr(pages, "_retry_expanded_sections_for_content_audit", no_retry)
    monkeypatch.setattr(pages, "should_abort_for_content_audit", lambda _issues: False)
    monkeypatch.setattr(pages, "_estimate_nodes_v2", lambda *_a, **_k: 10)
    published = []

    async def publish(**kwargs):
        published.append(kwargs)
        return "https://telegra.ph/expanded"

    monkeypatch.setattr(pages, "_publish_expanded_page", publish)
    sleeps = []

    async def sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(pages.asyncio, "sleep", sleep)
    return calls, published, sleeps


def _run_pipeline(fallback_fn=None):
    return _run(
        pages._run_expanded_pipeline,
        "StudyAnalysis",
        "prompt",
        "Study",
        "Title — Subtitle",
        "Author",
        fallback_fn=fallback_fn,
        yt_url="https://youtu.be/video",
        ai_data={"real_title": "Source"},
        video_id="video",
    )


def test_expanded_pipeline_publishes_audited_retry_result(monkeypatch):
    raw = json.dumps(
        {
            "outline": [{"title": "old", "time": "1:00"}],
            "sections": [{"title": "A", "content": "Text"}],
        }
    )
    _calls, published, _sleeps = _prepare_run(monkeypatch, [raw])
    monkeypatch.setattr(
        pages,
        "audit_expanded_sections",
        lambda sections, outline, **_kwargs: (sections, outline, ["before"]),
    )

    async def retry(**kwargs):
        return kwargs["sections"], kwargs["outline"], ["after"]

    monkeypatch.setattr(pages, "_retry_expanded_sections_for_content_audit", retry)
    assert _run_pipeline() == "https://telegra.ph/expanded"
    assert published[0]["page_title"] == "Study: Title — Subtitle"
    assert published[0]["outline"] == [{"title": "A", "time": "1:00"}]
    assert published[0]["video_id"] == "video"


def test_expanded_pipeline_retries_broken_json_and_rebuilds_outline(monkeypatch):
    raw = json.dumps({"outline": [], "sections": [{"title": "A", "content": "Text"}]})
    calls, published, sleeps = _prepare_run(monkeypatch, ["broken", raw])

    def estimate(*_args, **_kwargs):
        raise ValueError("cannot estimate")

    monkeypatch.setattr(pages, "_estimate_nodes_v2", estimate)
    assert _run_pipeline() == "https://telegra.ph/expanded"
    assert len(calls) == 2
    assert sleeps == [5]
    assert published[0]["outline"] == [{"title": "A", "time": ""}]


def test_expanded_pipeline_empty_response_uses_fallback(monkeypatch):
    _prepare_run(monkeypatch, [""])

    async def fallback():
        return "compact"

    assert _run_pipeline(fallback) == "compact"


@pytest.mark.parametrize(
    "sections",
    [{}, [{}], [{"title": "A"}]],
)
def test_expanded_pipeline_invalid_sections_use_fallback(monkeypatch, sections):
    raw = json.dumps({"outline": [], "sections": sections})
    _prepare_run(monkeypatch, [raw])

    async def fallback():
        return "compact"

    assert _run_pipeline(fallback) == "compact"


def test_expanded_pipeline_strict_audit_and_publish_failure_use_fallback(monkeypatch):
    raw = json.dumps({"outline": [], "sections": [{"title": "A", "content": "Text"}]})
    _prepare_run(monkeypatch, [raw])
    monkeypatch.setattr(
        pages,
        "audit_expanded_sections",
        lambda sections, outline, **_kwargs: (sections, outline, ["fatal"]),
    )
    monkeypatch.setattr(pages, "should_abort_for_content_audit", lambda _issues: True)

    async def fallback():
        return "compact"

    assert _run_pipeline(fallback) == "compact"

    _prepare_run(monkeypatch, [raw])

    async def fail_publish(**_kwargs):
        return None

    monkeypatch.setattr(pages, "_publish_expanded_page", fail_publish)
    assert _run_pipeline(fallback) == "compact"


def test_expanded_pipeline_unexpected_error_uses_fallback(monkeypatch):
    _prepare_run(monkeypatch, [])

    def explode(*_args):
        raise RuntimeError("adaptive failure")

    monkeypatch.setattr(pages, "get_adaptive_text_generation_params", explode)

    async def fallback():
        return "compact"

    assert _run_pipeline(fallback) == "compact"
