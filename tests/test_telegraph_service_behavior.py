"""Executed boundaries for Telegraph node cleanup and page publication."""

import asyncio
import json

import pytest
import requests

from converters import telegraph_nodes
from services import telegraph
from services.telegraph_edit import TelegraphEditResult


def _run(callback, *args, **kwargs):
    return asyncio.run(callback(*args, **kwargs))


@pytest.mark.parametrize(
    ("name", "enabled", "default"),
    [
        ("SYNOPSIS_DENSITY_RETRY", telegraph._synopsis_density_retry_enabled, True),
        ("SYNOPSIS_STRUCTURED", telegraph._synopsis_structured_output_enabled, False),
        ("SYNOPSIS_VERBATIM_PROMPT", telegraph._synopsis_verbatim_prompt_enabled, True),
    ],
)
def test_synopsis_feature_flags(monkeypatch, name, enabled, default):
    monkeypatch.delenv(name, raising=False)
    assert enabled() is default
    monkeypatch.setenv(name, "off")
    assert enabled() is False
    monkeypatch.setenv(name, "yes")
    assert enabled() is True


def test_synopsis_grounding_helper_stores_bounded_verbatim_text():
    data = {}
    telegraph._attach_synopsis_grounding(
        data,
        [
            {"title": "First", "time": "00:01", "content": "a" * 20000},
            {"title": "Second", "time": "00:02", "content": "b" * 5000},
        ],
    )

    grounding = data["_synopsis_grounding"]
    assert len(grounding) == 24000
    assert grounding.startswith("### First (00:01)\n" + "a" * 100)
    assert "### Second (00:02)" in grounding


def test_synopsis_grounding_helper_survives_malformed_sections():
    data = {"existing": "kept"}
    telegraph._attach_synopsis_grounding(data, [object()])
    assert data == {"existing": "kept"}


def test_classified_edit_requires_token_and_honors_strict_audit(monkeypatch):
    monkeypatch.setattr(telegraph, "TELEGRAPH_TOKEN", "")
    result = _run(
        telegraph._edit_telegraph_page_classified,
        "https://telegra.ph/Page",
        "Title",
        "Author",
        [],
        None,
    )
    assert result == TelegraphEditResult(ok=False, error="no_token", retryable=False)

    monkeypatch.setattr(telegraph, "TELEGRAPH_TOKEN", "token")
    monkeypatch.setattr(telegraph, "_clean_telegraph_nodes", lambda nodes: nodes)
    monkeypatch.setattr(telegraph, "_postprocess_telegraph_nodes", lambda nodes: nodes)
    monkeypatch.setattr(telegraph, "_final_telegraph_polish", lambda nodes: nodes)
    monkeypatch.setattr(telegraph, "audit_telegraph_page", lambda *_a, **_k: ["bad"])
    monkeypatch.setattr(telegraph, "format_audit_issues", lambda _issues: "unsafe")
    monkeypatch.setattr(telegraph, "should_abort_for_page_audit", lambda _issues: True)

    result = _run(
        telegraph._edit_telegraph_page_classified,
        "https://telegra.ph/Page",
        "Title",
        "Author",
        [],
        None,
    )
    assert result.ok is False
    assert result.error == "page_audit_strict: unsafe"
    assert result.retryable is False


def test_classified_edit_survives_cache_invalidation_failure(monkeypatch):
    from services import pdf_generator

    monkeypatch.setattr(telegraph, "TELEGRAPH_TOKEN", "token")
    monkeypatch.setattr(telegraph, "_clean_telegraph_nodes", lambda nodes: nodes)
    monkeypatch.setattr(telegraph, "_postprocess_telegraph_nodes", lambda nodes: nodes)
    monkeypatch.setattr(telegraph, "_final_telegraph_polish", lambda nodes: nodes)
    monkeypatch.setattr(telegraph, "audit_telegraph_page", lambda *_a, **_k: [])

    async def retry(_operation, **_kwargs):
        return TelegraphEditResult(ok=True)

    def invalidate(_url):
        raise RuntimeError("cache unavailable")

    monkeypatch.setattr(telegraph, "run_telegraph_edit_with_retry", retry)
    monkeypatch.setattr(pdf_generator, "invalidate_telegraph_url", invalidate)
    result = _run(
        telegraph._edit_telegraph_page_classified,
        "https://telegra.ph/Page",
        "Title",
        "Author",
        [],
        None,
    )
    assert result.ok is True


def test_demote_paragraph_bold_preserves_markup_and_demotes_only_long_blocks():
    assert telegraph._demote_paragraph_bold("") == ""
    assert telegraph._demote_paragraph_bold("plain") == "plain"
    assert telegraph._demote_paragraph_bold("**a** || **b**") == "**a** || **b**"
    assert telegraph._demote_paragraph_bold("**broken") == "**broken"
    bullet = "• **Term — definition**"
    assert telegraph._demote_paragraph_bold(bullet) == bullet
    short = "**short**"
    assert telegraph._demote_paragraph_bold(short) == short

    long = "x" * 121
    mixed = f"**{long}** and **short**"
    assert telegraph._demote_paragraph_bold(mixed) == f"{long} and **short**"

    bold = "x" * 100
    assert telegraph._demote_paragraph_bold(f"**{bold}**") == bold
    accent = f"**{'x' * 20}** {'y' * 80}"
    assert telegraph._demote_paragraph_bold(accent) == accent


def test_markdown_conversion_handles_headings_spacing_and_rules(monkeypatch):
    monkeypatch.setattr(telegraph_nodes, "_scrub_inline", lambda text: "" if text == "SCRUB" else text)
    markdown = "\n".join(
        [
            "Cocoon AI Summary",
            "SCRUB",
            "---",
            "## Heading 2",
            "",
            "Body after heading",
            "### Heading 3",
            "# Heading 1",
            "#### Heading 4",
            "##### Heading 5",
            "###### Heading 6",
            "…",
        ]
    )
    nodes = telegraph._md_to_telegraph_nodes(markdown)

    assert nodes[0] == {"tag": "hr"}
    assert {node["tag"] for node in nodes if node["tag"] in {"h3", "h4"}} == {
        "h3",
        "h4",
    }
    assert {"tag": "p", "children": [{"tag": "br"}]} in nodes
    assert all("Cocoon" not in json.dumps(node) for node in nodes)
    assert all("SCRUB" not in json.dumps(node) for node in nodes)


def test_markdown_conversion_preserves_rtl_and_numbered_bold_contracts(monkeypatch):
    parse_inline = telegraph._md_parse_inline

    def parse_with_plain_fallback(text):
        if text.startswith("**unclosed") or text.startswith("**שלום unclosed"):
            return ["plain fallback"]
        return parse_inline(text)

    monkeypatch.setattr(telegraph, "_md_parse_inline", parse_with_plain_fallback)
    markdown = "\n".join(
        [
            "> plain quote",
            "> שלום",
            "1. **Only title**",
            "2. **שלום**",
            "3. **Bold** continuation",
            "4. **שלום** continuation",
            "5. **unclosed",
            "6. **שלום unclosed",
            "7. plain numbered",
            "8. שלום",
            "- bullet",
            "- שלום",
            "* star bullet",
            "**שלום** paragraph",
        ]
    )
    nodes = telegraph._md_to_telegraph_nodes(markdown)
    encoded = json.dumps(nodes, ensure_ascii=False)

    assert nodes[0]["tag"] == "blockquote"
    assert nodes[1]["attrs"] == {"dir": "ltr"}
    assert {"tag": "b", "children": ["1. Only title."]} in nodes[2]["children"]
    assert nodes[3]["attrs"] == {"dir": "ltr"}
    assert nodes[4]["children"][0]["children"][0] == "3. "
    assert nodes[5]["attrs"] == {"dir": "ltr"}
    assert nodes[9]["attrs"] == {"dir": "ltr"}
    assert nodes[11]["attrs"] == {"dir": "ltr"}
    assert nodes[-1]["children"][0] == "\u200e"
    assert "• " in encoded


def test_markdown_conversion_falls_back_to_original_text():
    assert telegraph._md_to_telegraph_nodes("\n.\n") == [{"tag": "p", "children": ["\n.\n"]}]


def test_text_and_recursive_node_cleanup_preserve_real_content():
    assert telegraph._clean_text_block("") == ""
    assert telegraph._clean_text_block("Cocoon AI Summary") == ""
    assert telegraph._clean_text_block("  real\ntext  ") == "  real\ntext  "
    assert telegraph._clean_node_texts("text") == "text"
    assert telegraph._clean_node_texts({"tag": "hr"}) == {"tag": "hr"}

    node = {
        "tag": "p",
        "children": [
            "keep",
            None,
            4,
            {"tag": "span", "children": []},
            {"tag": "span", "children": ["nested"]},
        ],
    }
    assert telegraph._clean_node_texts(node) == {
        "tag": "p",
        "children": ["keep", 4, {"tag": "span", "children": ["nested"]}],
    }
    assert telegraph._clean_node_texts({"tag": "p", "children": []}) is None


def test_clean_telegraph_nodes_drops_meta_and_leading_dates(monkeypatch):
    monkeypatch.setattr(telegraph_nodes, "is_meta_garbage", lambda text: text in {"", "META"})
    nodes = [
        None,
        {"tag": "p", "children": ["META"]},
        {"tag": "p", "children": [{"tag": "b", "children": ["nested"]}]},
        {"tag": "p", "children": ["March 15 at 18:55"]},
        {"tag": "hr"},
        {"tag": "p", "children": ["real"]},
    ]
    assert telegraph._clean_telegraph_nodes(nodes) == [
        {"tag": "hr"},
        {"tag": "p", "children": ["real"]},
    ]


class Response:
    def __init__(self, status=200, payload=None, json_error=None):
        self.status_code = status
        self.payload = payload
        self.json_error = json_error

    def json(self):
        if self.json_error is not None:
            raise self.json_error
        return self.payload


def _prepare_post(monkeypatch, responder, *, token="token"):
    calls = []
    sleeps = []

    def post(_url, **kwargs):
        calls.append(kwargs)
        outcome = responder(kwargs, len(calls))
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    async def sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(telegraph, "TELEGRAPH_TOKEN", token)
    monkeypatch.setattr(telegraph, "_final_telegraph_polish", lambda nodes: nodes)
    monkeypatch.setattr(telegraph, "_clean_telegraph_nodes", lambda nodes: nodes)
    monkeypatch.setattr(telegraph.requests, "post", post)
    monkeypatch.setattr(telegraph.asyncio, "sleep", sleep)
    return calls, sleeps


def _post(title="Title", nodes=None, author_url=""):
    async def invoke():
        return await telegraph._telegraph_post(
            title,
            "Author",
            [{"tag": "p", "children": ["Text"]}] if nodes is None else nodes,
            asyncio.get_running_loop(),
            author_url,
        )

    return _run(invoke)


def test_post_cleans_dirty_fields_and_returns_url(monkeypatch):
    def respond(_kwargs, _count):
        return Response(payload={"ok": True, "result": {"url": "https://telegra.ph/ok"}})

    calls, _sleeps = _prepare_post(monkeypatch, respond)
    nodes = [
        {"tag": "p", "children": ["Cocoon AI Summary"]},
        {"tag": "p", "children": []},
        {"tag": "p", "children": ["real"]},
    ]
    assert _post("Cocoon AI Summary Title", nodes) == "https://telegra.ph/ok"
    assert calls[0]["json"]["title"] == "Title"
    assert calls[0]["json"]["content"] == [
        {"tag": "p", "children": [""]},
        {"tag": "p", "children": ["real"]},
    ]


def test_post_uses_pre_cleaned_nodes_when_dirty_cleanup_fails(monkeypatch):
    def respond(_kwargs, _count):
        return Response(payload={"ok": True, "result": {"url": "https://telegra.ph/fallback"}})

    def fail_dump(*_args, **_kwargs):
        raise ValueError("unserializable node")

    calls, _sleeps = _prepare_post(monkeypatch, respond)
    monkeypatch.setattr(telegraph.json, "dumps", fail_dump)
    nodes = [{"tag": "p", "children": ["already cleaned"]}]

    assert _post("Title", nodes) == "https://telegra.ph/fallback"
    assert calls[0]["json"]["content"] == nodes


def test_post_without_token_or_with_unserializable_nodes_returns_none(monkeypatch):
    def must_not_post(_kwargs, _count):
        raise AssertionError("network must not be called")

    calls, _ = _prepare_post(monkeypatch, must_not_post, token="")
    assert _post() is None
    assert calls == []
    assert _post(nodes=[{"tag": "p", "children": [object()]}]) is None
    assert calls == []


def test_post_terminal_invalid_json_and_http_exhaustion(monkeypatch):
    _calls, _ = _prepare_post(
        monkeypatch,
        lambda _kwargs, _count: Response(json_error=ValueError("not json")),
    )
    assert _post() is None

    _calls, sleeps = _prepare_post(
        monkeypatch,
        lambda _kwargs, _count: Response(status=503, payload={"ok": False, "error": "upstream"}),
    )
    assert _post() is None
    assert sleeps == [1, 2]


def test_post_network_exhaustion_and_unexpected_error(monkeypatch):
    _calls, sleeps = _prepare_post(
        monkeypatch,
        lambda _kwargs, _count: requests.ConnectionError("offline"),
    )
    assert _post() is None
    assert sleeps == [1, 2]

    _calls, sleeps = _prepare_post(monkeypatch, lambda _kwargs, _count: RuntimeError("unexpected"))
    assert _post() is None
    assert sleeps == []


def test_post_flood_wait_uses_bounded_server_delay(monkeypatch):
    def respond(_kwargs, count):
        if count == 1:
            return Response(payload={"ok": False, "error": "FLOOD_WAIT_99"})
        return Response(payload={"ok": True, "result": {"url": "https://telegra.ph/after"}})

    _calls, sleeps = _prepare_post(monkeypatch, respond)
    assert _post() == "https://telegra.ph/after"
    assert sleeps == [31]


def test_post_deterministic_api_error_is_not_retried(monkeypatch):
    calls, sleeps = _prepare_post(
        monkeypatch,
        lambda _kwargs, _count: Response(payload={"ok": False, "error": "BAD_REQUEST"}),
    )
    assert _post() is None
    assert len(calls) == 1
    assert sleeps == []


def test_content_too_big_recurses_and_builds_partial_toc(monkeypatch):
    def respond(kwargs, _count):
        title = kwargs["json"]["title"]
        if title in {"Title", "Title (1)"}:
            return Response(payload={"ok": False, "error": "CONTENT_TOO_BIG"})
        if title == "Title (2)":
            return Response(payload={"ok": False, "error": "BAD_PART"})
        if title == "Title — Содержание":
            return Response(payload={"ok": True, "result": {"url": "https://telegra.ph/toc"}})
        label = title.removeprefix("Title (").removesuffix(")")
        return Response(payload={"ok": True, "result": {"url": f"https://telegra.ph/{label}"}})

    calls, _sleeps = _prepare_post(monkeypatch, respond)
    nodes = [
        {"tag": "p", "children": ["one"]},
        {"tag": "p", "children": ["two"]},
        {"tag": "h3", "children": ["heading"]},
        {"tag": "p", "children": ["three"]},
    ]
    assert _post(nodes=nodes) == "https://telegra.ph/toc"
    titles = [call["json"]["title"] for call in calls]
    assert titles == [
        "Title",
        "Title (1)",
        "Title (1.1)",
        "Title (1.2)",
        "Title (2)",
        "Title — Содержание",
    ]
    toc = calls[-1]["json"]["content"]
    assert [child["attrs"]["href"] for child in toc[1:] for child in child["children"]] == [
        "https://telegra.ph/1.1",
        "https://telegra.ph/1.2",
    ]


def test_content_too_big_returns_none_when_no_part_publishes(monkeypatch):
    _prepare_post(
        monkeypatch,
        lambda _kwargs, _count: (
            Response(payload={"ok": False, "error": "CONTENT_TOO_BIG"})
            if _count == 1
            else Response(payload={"ok": False, "error": "BAD_PART"})
        ),
    )
    nodes = [{"tag": "p", "children": ["one"]}, {"tag": "p", "children": ["two"]}]
    assert _post(nodes=nodes) is None


def test_content_too_big_returns_only_successful_part(monkeypatch):
    def respond(kwargs, count):
        title = kwargs["json"]["title"]
        if count == 1 or title == "Title (1)":
            error = "CONTENT_TOO_BIG" if count == 1 else "BAD_PART"
            return Response(payload={"ok": False, "error": error})
        return Response(payload={"ok": True, "result": {"url": "https://telegra.ph/only"}})

    _prepare_post(monkeypatch, respond)
    nodes = [{"tag": "p", "children": ["one"]}, {"tag": "p", "children": ["two"]}]
    assert _post(nodes=nodes) == "https://telegra.ph/only"
