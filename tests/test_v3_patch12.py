"""Tests for v3 patch 12 — editPage reliability: API error logging + exponential backoff.

Changes:
- converters/md_telegraph.py: log Telegraph API error message when editPage returns ok=False
- services/telegraph_pages.py: exponential backoff in editPage retry (3s, 6s, 12s)
"""


def test_edit_telegraph_page_logs_api_error():
    src = open("converters/md_telegraph.py", encoding="utf-8").read()
    assert "API returned ok=False, error=" in src, \
        "md_telegraph must log Telegraph API error message on editPage failure"


def test_edit_telegraph_page_logs_url_on_failure():
    src = open("converters/md_telegraph.py", encoding="utf-8").read()
    assert "_edit_telegraph_page: API returned ok=False" in src, \
        "md_telegraph must include structured log for editPage failure"


def test_telegraph_pages_exponential_backoff():
    src = open("services/telegraph_pages.py", encoding="utf-8").read()
    assert "3 * (2 ** retry_attempt)" in src, \
        "telegraph_pages editPage retry must use exponential backoff"


def test_telegraph_pages_backoff_logged():
    src = open("services/telegraph_pages.py", encoding="utf-8").read()
    assert "жду %dс" in src or "жду {" in src, \
        "telegraph_pages must log backoff duration"


def test_synopsis_already_has_exponential_backoff():
    """Synopsis editPage in telegraph.py already had exponential backoff — must stay."""
    src = open("services/telegraph.py", encoding="utf-8").read()
    assert "3 * (2 ** retry_attempt)" in src, \
        "telegraph.py Synopsis editPage must have exponential backoff"

