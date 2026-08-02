"""Tests for v3 patch 12 — editPage reliability and retry classification.

Legacy editPage loops used source-local 3/6/12 sleeps. Synopsis now delegates to
``services.telegraph_edit`` so deterministic API failures such as
CONTENT_TOO_BIG return immediately while transient failures retain exponential
backoff.
"""

from services.telegraph_edit import TelegraphEditResult, telegraph_retry_delay


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


def test_synopsis_retry_policy_is_centralized_and_exponential_for_transient_failures():
    src = open("services/telegraph.py", encoding="utf-8").read()
    assert "run_telegraph_edit_with_retry" in src
    transient = TelegraphEditResult(ok=False, error="upstream", retryable=True)
    assert [telegraph_retry_delay(transient, i) for i in range(3)] == [3, 6, 12]


def test_synopsis_deterministic_overflow_has_no_retry_delay():
    overflow = TelegraphEditResult(
        ok=False,
        error="CONTENT_TOO_BIG",
        retryable=False,
    )
    assert telegraph_retry_delay(overflow, 0) == 0
