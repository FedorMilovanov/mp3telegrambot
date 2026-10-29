"""Regression test: Gemini proxy auto-fallback from TELEGRAM_PROXY_URL."""

import ast
from pathlib import Path


def test_globals_has_gemini_proxy_fallback() -> None:
    """globals.py must auto-set HTTPS_PROXY from TELEGRAM_PROXY_URL when not explicit.

    Without this, Gemini API calls from Russia get 400 FAILED_PRECONDITION
    'User location is not supported' because httpx goes direct.
    """
    src = Path("core/globals.py").read_text(encoding="utf-8")
    assert "TELEGRAM_PROXY_URL" in src, "globals.py must fallback HTTPS_PROXY from TELEGRAM_PROXY_URL"
    assert "GEMINI_PROXY_URL" in src, "globals.py must support explicit GEMINI_PROXY_URL"
    assert 'os.environ["HTTPS_PROXY"]' in src, "globals.py must set HTTPS_PROXY env var for httpx/google-genai"


def test_env_example_documents_gemini_proxy() -> None:
    env = Path(".env.example").read_text(encoding="utf-8")
    assert "GEMINI_PROXY_URL" in env, ".env.example must document GEMINI_PROXY_URL"


def test_auto_proxy_excludes_russian_services() -> None:
    """When HTTPS_PROXY is auto-set from TELEGRAM_PROXY_URL, Russian services
    must be added to NO_PROXY so requests.get to RuTube/VK goes direct.

    Telegraph (telegra.ph / api.telegra.ph) intentionally goes THROUGH the
    proxy since commit a064309 — Telegraph can be blocked locally, so it must
    NOT be in the auto NO_PROXY list."""
    src = Path("core/globals.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    assignment = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "_auto_no_proxy" for target in node.targets)
    )
    hosts = set(ast.literal_eval(assignment.value).split(","))
    assert {"rutube.ru", "api.vk.com", "api.telegram.org"} <= hosts
    assert "telegra.ph" not in hosts
    assert "api.telegra.ph" not in hosts
    assert "_proxy_was_auto" in src, "must track whether proxy was auto-set"
