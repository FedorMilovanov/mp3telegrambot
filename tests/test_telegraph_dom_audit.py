from pathlib import Path

from core.telegraph_dom_audit import audit_telegraph_html, summarize_dom_issues
from tools.audit_telegraph_pages import extract_telegraph_urls


def test_dom_audit_catches_visible_editorial_artifacts():
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


def test_extract_telegraph_urls_dedupes_markdown(tmp_path):
    p = tmp_path / "archive.md"
    p.write_text(
        "- A: https://telegra.ph/Page-1\n- B: https://telegra.ph/Page-1\n- C: https://example.com/no\n",
        encoding="utf-8",
    )
    assert extract_telegraph_urls(p) == ["https://telegra.ph/Page-1"]


def test_audit_tool_documents_playwright_usage():
    src = Path("tools/audit_telegraph_pages.py").read_text(encoding="utf-8")
    assert "playwright.async_api" in src
    assert "requests fallback" in src or "requests" in src


def test_dom_audit_ignores_telegraph_chrome_anchor_without_href():
    issues = audit_telegraph_html("<html><body><a>Telegraph</a><p>Чистый текст.</p></body></html>")
    assert "bad_links" not in {i.code for i in issues}


def test_dom_audit_catches_author_surname_third_person_but_not_missing_href():
    html = "<html><body><a>Telegraph chrome</a><p>Данкан подчеркивает этот смысловой пласт.</p></body></html>"
    issues = audit_telegraph_html(html)
    codes = {i.code for i in issues}
    assert "third_person_wrapper" in codes
    assert "bad_links" not in codes


def test_audit_tool_supports_url_file_argument():
    src = Path("tools/audit_telegraph_pages.py").read_text(encoding="utf-8")
    assert "--url-file" in src
    assert "extract_telegraph_urls(args.url_file)" in src


def test_repair_tool_reports_failed_edit_and_supports_fail_on_unresolved():
    src = Path("tools/repair_telegraph_pages.py").read_text(encoding="utf-8")
    assert "editPage_failed_or_no_telegraph_token" in src
    assert "--fail-on-unresolved" in src
    assert "args.fail_on_unresolved" in src
