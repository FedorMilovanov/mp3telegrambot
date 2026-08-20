from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from services import youtube_po_token_warmup as warmup
from services.youtube_po_token_runtime import (
    BGUTIL_EXPECTED_COMMIT,
    YouTubePoTokenRuntime,
)


def _runtime(provider_home: Path) -> YouTubePoTokenRuntime:
    return YouTubePoTokenRuntime(
        provider_version="1.3.1",
        provider_commit=BGUTIL_EXPECTED_COMMIT,
        node_version="22.23.1",
        provider_home=provider_home,
        plugin_root=provider_home.parent / "plugin",
    )


def test_warmup_runs_exact_production_script_before_main_loop(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    provider_home = tmp_path / "provider" / "server"
    script = provider_home / "build" / "generate_once.js"
    script.parent.mkdir(parents=True)
    script.write_text("// test", encoding="utf-8")
    captured: dict[str, object] = {}

    monkeypatch.setattr(warmup.shutil, "which", lambda name: "node.exe" if name == "node" else None)

    async def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout="1.3.1\n", stderr="")

    monkeypatch.setattr(warmup, "run_cancellable_process", fake_run)

    result = warmup.warm_youtube_po_token_provider(
        _runtime(provider_home),
        timeout_seconds=60,
    )

    assert captured["command"] == ["node.exe", str(script.resolve()), "--version"]
    assert captured["kwargs"]["cwd"] == provider_home.resolve()
    assert captured["kwargs"]["timeout"] == 60.0
    assert captured["kwargs"]["text"] is True
    assert result.provider_version == "1.3.1"
    assert result.elapsed_seconds >= 0
    assert result.status_text().startswith("warmup=")


def test_warmup_timeout_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    provider_home = tmp_path / "provider" / "server"
    script = provider_home / "build" / "generate_once.js"
    script.parent.mkdir(parents=True)
    script.write_text("// test", encoding="utf-8")
    monkeypatch.setattr(warmup.shutil, "which", lambda _name: "node")

    async def fake_run(command, **kwargs):
        raise subprocess.TimeoutExpired(command, timeout=kwargs["timeout"])

    monkeypatch.setattr(warmup, "run_cancellable_process", fake_run)

    with pytest.raises(warmup.YouTubePoTokenWarmupError, match="60s"):
        warmup.warm_youtube_po_token_provider(_runtime(provider_home))


def test_warmup_rejects_wrong_provider_version(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    provider_home = tmp_path / "provider" / "server"
    script = provider_home / "build" / "generate_once.js"
    script.parent.mkdir(parents=True)
    script.write_text("// test", encoding="utf-8")
    monkeypatch.setattr(warmup.shutil, "which", lambda _name: "node")

    async def fake_run(_command, **_kwargs):
        return SimpleNamespace(returncode=0, stdout="9.9.9\n", stderr="")

    monkeypatch.setattr(warmup, "run_cancellable_process", fake_run)

    with pytest.raises(warmup.YouTubePoTokenWarmupError, match="unexpected|неожиданную"):
        warmup.warm_youtube_po_token_provider(_runtime(provider_home))


def test_bot_entrypoint_warms_provider_before_runtime_bootstrap() -> None:
    entry = Path("bot_new.py").read_text(encoding="utf-8")
    require_index = entry.index("require_youtube_po_token_runtime()")
    warmup_index = entry.index("warm_youtube_po_token_provider(_youtube_po_runtime)")
    bootstrap_index = entry.index("bootstrap_pre_main()")

    assert require_index < warmup_index < bootstrap_index
    assert "✅ YouTube PO Token" in entry
