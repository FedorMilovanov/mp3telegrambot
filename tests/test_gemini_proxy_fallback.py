"""Regression test: Gemini proxy auto-fallback from TELEGRAM_PROXY_URL."""
from pathlib import Path


def test_globals_has_gemini_proxy_fallback():
    """globals.py must auto-set HTTPS_PROXY from TELEGRAM_PROXY_URL when not explicit.

    Without this, Gemini API calls from Russia get 400 FAILED_PRECONDITION
    'User location is not supported' because httpx goes direct.
    """
    src = Path("core/globals.py").read_text(encoding="utf-8")
    assert "TELEGRAM_PROXY_URL" in src, (
        "globals.py must fallback HTTPS_PROXY from TELEGRAM_PROXY_URL"
    )
    assert "GEMINI_PROXY_URL" in src, (
        "globals.py must support explicit GEMINI_PROXY_URL"
    )
    assert 'os.environ["HTTPS_PROXY"]' in src, (
        "globals.py must set HTTPS_PROXY env var for httpx/google-genai"
    )


def test_env_example_documents_gemini_proxy():
    env = Path(".env.example").read_text(encoding="utf-8")
    assert "GEMINI_PROXY_URL" in env, (
        ".env.example must document GEMINI_PROXY_URL"
    )


def test_auto_proxy_excludes_russian_services():
    """When HTTPS_PROXY is auto-set from TELEGRAM_PROXY_URL, Russian services
    must be added to NO_PROXY so requests.get to RuTube/VK goes direct.

    Telegraph (telegra.ph / api.telegra.ph) intentionally goes THROUGH the
    proxy since commit a064309 — Telegraph can be blocked locally, so it must
    NOT be in the auto NO_PROXY list."""
    src = Path("core/globals.py").read_text(encoding="utf-8")
    assert "rutube.ru" in src, "NO_PROXY must include rutube.ru"
    assert "api.vk.com" in src, "NO_PROXY must include api.vk.com"
    assert "telegra.ph" not in src, (
        "telegra.ph must stay OUT of auto NO_PROXY — Telegraph goes through proxy (a064309)"
    )
    assert "api.telegram.org" in src, "NO_PROXY must include api.telegram.org"
    assert "_proxy_was_auto" in src, "must track whether proxy was auto-set"
