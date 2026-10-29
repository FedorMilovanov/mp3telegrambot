import pytest
import asyncio
from pathlib import Path
from typing import Any

from core.telegraph_dom_audit import audit_telegraph_html, summarize_dom_issues
from tools.audit_telegraph_pages import extract_telegraph_urls


def test_dom_audit_catches_visible_editorial_artifacts() -> None:
    html = """
    <html><body><article>
      <p>Лектор показывает, как легко подменить верность внешними атрибутами.</p>
      <p>-* *Ефесянам 5:25** / / сырой **markdown**</p>
      <p>• Умерщвление греха, Джон Оуэн (Of the Mortification of Sin, John Owen).</p>
      <a href="#">bad</a>
    </article></body></html>
    """
    issues = audit_telegraph_html(html, url="https://telegra.ph/x")
    codes = {i.code for i in issues}
    assert "third_person_wrapper" in codes
    assert "markdown_artifact" in codes
    assert "source_map_original_title" in codes
    assert "bad_links" in codes
    assert "pages_with_issues=1" in summarize_dom_issues([("u", issues)])


def test_extract_telegraph_urls_dedupes_markdown(tmp_path: Path) -> None:
    p = tmp_path / "archive.md"
    p.write_text(
        "- A: https://telegra.ph/Page-1\n- B: https://telegra.ph/Page-1\n- C: https://example.com/no\n",
        encoding="utf-8",
    )
    assert extract_telegraph_urls(p) == ["https://telegra.ph/Page-1"]


def test_audit_tool_documents_playwright_usage() -> None:
    src = Path("tools/audit_telegraph_pages.py").read_text(encoding="utf-8")
    assert "playwright.async_api" in src
    assert "requests fallback" in src or "requests" in src


def test_dom_audit_ignores_telegraph_chrome_anchor_without_href() -> None:
    issues = audit_telegraph_html("<html><body><a>Telegraph</a><p>Чистый текст.</p></body></html>")
    assert "bad_links" not in {i.code for i in issues}


def test_dom_audit_catches_author_surname_third_person_but_not_missing_href() -> None:
    html = "<html><body><a>Telegraph chrome</a><p>Данкан подчеркивает этот смысловой пласт.</p></body></html>"
    issues = audit_telegraph_html(html)
    codes = {i.code for i in issues}
    assert "third_person_wrapper" in codes
    assert "bad_links" not in codes


def test_audit_tool_supports_url_file_argument() -> None:
    src = Path("tools/audit_telegraph_pages.py").read_text(encoding="utf-8")
    assert "--url-file" in src
    assert "extract_telegraph_urls(args.url_file)" in src


def test_repair_tool_reports_failed_edit_and_supports_fail_on_unresolved() -> None:
    src = Path("tools/repair_telegraph_pages.py").read_text(encoding="utf-8")
    assert "editPage_failed_or_no_telegraph_token" in src
    assert "--fail-on-unresolved" in src
    assert "args.fail_on_unresolved" in src


def test_repair_tool_nodes_signature_is_deterministic() -> None:
    from tools import repair_telegraph_pages

    first = {"b": [1], "a": "ё"}
    second = {"a": "ё", "b": [1]}
    assert repair_telegraph_pages._nodes_signature(first) == '{"a":"ё","b":[1]}'
    assert repair_telegraph_pages._nodes_signature(second) == repair_telegraph_pages._nodes_signature(first)


def test_audit_tool_can_write_repair_targets(tmp_path: Path) -> None:
    from core.telegraph_dom_audit import TelegraphDomIssue
    from tools.audit_telegraph_pages import write_repair_targets

    out = tmp_path / "targets.md"
    count = write_repair_targets(
        out,
        [
            ("https://telegra.ph/bad", [TelegraphDomIssue("third_person_wrapper", "x")]),
            ("https://telegra.ph/good", []),
        ],
    )
    text = out.read_text(encoding="utf-8")
    assert count == 1
    assert "https://telegra.ph/bad" in text
    assert "third_person_wrapper" in text
    assert "https://telegra.ph/good" not in text


def test_audit_tool_extracts_next_part_urls_from_nodes() -> None:
    from tools.audit_telegraph_pages import _extract_next_part_urls_from_nodes

    nodes = [
        {
            "tag": "p",
            "children": [
                {"tag": "a", "attrs": {"href": "/Part-2"}, "children": ["➡ Дальше: [2/3]"]},
            ],
        }
    ]
    assert _extract_next_part_urls_from_nodes(nodes) == ["https://telegra.ph/Part-2"]


def test_audit_and_repair_tools_expand_multipart_chains_by_default() -> None:
    audit_src = Path("tools/audit_telegraph_pages.py").read_text(encoding="utf-8")
    repair_src = Path("tools/repair_telegraph_pages.py").read_text(encoding="utf-8")
    assert "expand_input_urls(urls, expand_chains=not args.no_expand_chains" in audit_src
    assert "--no-expand-chains" in audit_src
    assert "expand_telegraph_page_chain" in repair_src
    assert "expand_chains=not args.no_expand_chains" in repair_src


def test_audit_chain_default_does_not_truncate_after_twelve_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tools import audit_telegraph_pages

    total = 15

    class Response:
        def __init__(self, payload: dict[str, Any]) -> None:
            self._payload = payload

        def json(self) -> dict[str, Any]:
            return self._payload

    def fake_get(url: str, **_kwargs: Any) -> Response:
        path = url.split("/getPage/", 1)[1].split("?", 1)[0]
        index = int(path.rsplit("-", 1)[1])
        children = []
        if index < total:
            children = [
                {
                    "tag": "a",
                    "attrs": {"href": f"/Audit-{index + 1}"},
                    "children": [f"➡ Дальше: [{index + 1}/{total}]"],
                }
            ]
        return Response({"ok": True, "result": {"content": [{"tag": "p", "children": children}]}})

    monkeypatch.setattr(audit_telegraph_pages.requests, "get", fake_get)
    chain = audit_telegraph_pages.expand_telegraph_url_chain_sync("https://telegra.ph/Audit-1")

    assert len(chain) == total
    assert chain[-1] == "https://telegra.ph/Audit-15"


def test_dom_audit_treats_prev_next_links_as_navigation() -> None:
    html = (
        "<html><body><article>" + ("длинный текст " * 400) + "<a href='/p2'>➡ Дальше: [2/4]</a></article></body></html>"
    )
    issues = audit_telegraph_html(html, url="https://telegra.ph/p1")
    assert "navigation_missing_warning" not in {i.code for i in issues}


def test_audit_chain_has_suffix_probe_for_outdated_first_part() -> None:
    from tools.audit_telegraph_pages import guess_telegraph_part_url

    assert guess_telegraph_part_url("https://telegra.ph/Page-06-17", 2) == "https://telegra.ph/Page-06-17-2"
    src = Path("tools/audit_telegraph_pages.py").read_text(encoding="utf-8")
    assert "guess_telegraph_part_url(start, 2)" in src


def test_audit_chain_survives_suffix_probe_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import audit_telegraph_pages

    calls: list[str] = []

    class Response:
        def json(self) -> dict[str, Any]:
            return {"ok": True, "result": {"content": []}}

    def fake_get(url: str, **_kwargs: Any) -> Response:
        calls.append(url)
        if "return_content=false" in url:
            raise RuntimeError("suffix probe unavailable")
        return Response()

    monkeypatch.setattr(audit_telegraph_pages.requests, "get", fake_get)
    chain = audit_telegraph_pages.expand_telegraph_url_chain_sync("https://telegra.ph/Probe-1")

    assert chain == ["https://telegra.ph/Probe-1"]
    assert len(calls) == 2


def test_dom_audit_falls_back_to_requests_when_playwright_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import audit_telegraph_pages

    async def fail_playwright(_url: str, _timeout_ms: int) -> None:
        raise RuntimeError("browser unavailable")

    monkeypatch.setattr(audit_telegraph_pages, "_fetch_with_playwright", fail_playwright)
    monkeypatch.setattr(audit_telegraph_pages, "_fetch_with_requests", lambda _url, _timeout: "requests html")

    result = asyncio.run(audit_telegraph_pages.fetch_page_html("https://telegra.ph/Page"))
    assert result == ("requests html", "requests")
