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
