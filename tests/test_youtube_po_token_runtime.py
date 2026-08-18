from __future__ import annotations

from importlib import metadata
from pathlib import Path

import pytest

from services import youtube_po_token_runtime as po


def test_require_youtube_po_token_runtime_reports_versions_and_browser(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    browser = tmp_path / "chrome.exe"
    browser.write_bytes(b"browser")
    versions = {
        po.WPC_DISTRIBUTION: "1.1.2",
        po.NODRIVER_DISTRIBUTION: "0.50.3",
    }
    monkeypatch.setattr(po.metadata, "version", lambda name: versions[name])
    monkeypatch.setattr(po, "_discover_chromium_browser", lambda: browser)

    runtime = po.require_youtube_po_token_runtime()

    assert runtime.provider_version == "1.1.2"
    assert runtime.nodriver_version == "0.50.3"
    assert runtime.browser_path == browser
    assert runtime.status_text() == "WPC 1.1.2; nodriver 0.50.3; browser=chrome.exe"


def test_missing_po_provider_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(_name: str) -> str:
        raise metadata.PackageNotFoundError(_name)

    monkeypatch.setattr(po.metadata, "version", missing)

    with pytest.raises(po.YouTubePoTokenRuntimeError, match="не установлен"):
        po.require_youtube_po_token_runtime()


def test_browser_failure_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(po.metadata, "version", lambda _name: "test")

    def fail_browser() -> Path:
        raise po.YouTubePoTokenRuntimeError("browser unavailable")

    monkeypatch.setattr(po, "_discover_chromium_browser", fail_browser)

    with pytest.raises(po.YouTubePoTokenRuntimeError, match="browser unavailable"):
        po.require_youtube_po_token_runtime()


def test_ytdlp_policy_uses_mweb_provider_without_manual_token_or_cookie_conflict() -> None:
    config = Path("yt-dlp.conf").read_text(encoding="utf-8")
    lower = config.lower()

    assert "youtube:player_client=mweb" in config
    assert "po_token=" not in lower
    assert "--cookies" not in lower
    assert "--cookies-from-browser" not in lower


def test_po_provider_dependencies_are_pinned() -> None:
    requirements = Path("requirements.txt").read_text(encoding="utf-8")
    lock = Path("requirements-lock.txt").read_text(encoding="utf-8")

    assert "yt-dlp-getpot-wpc==1.1.2" in requirements
    assert "nodriver==0.50.3" in requirements
    assert "yt-dlp-getpot-wpc==1.1.2" in lock
    assert "nodriver==0.50.3" in lock


def test_factory_max_quality_selectors_remain_fail_closed() -> None:
    source = Path("services/shorts_factory_source.py").read_text(encoding="utf-8")

    assert '"bestaudio/best"' in source
    assert '"bestvideo+bestaudio/best"' in source
    assert '"--abort-on-unavailable-fragments"' in source
    assert '"18"' not in source


def test_bot_entrypoint_requires_po_runtime_before_runtime_bootstrap() -> None:
    entry = Path("bot_new.py").read_text(encoding="utf-8")
    po_index = entry.index("require_youtube_po_token_runtime()")
    bootstrap_index = entry.index("bootstrap_pre_main()")

    assert po_index < bootstrap_index
    assert "format 18/360p fallback не используется" in entry
