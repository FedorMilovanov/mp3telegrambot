from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from tools import check_youtube_gvs as probe


def test_production_command_preserves_factory_quality_contract(monkeypatch, tmp_path):
    import services.ffmpeg as ff

    monkeypatch.setattr(
        ff,
        "_build_ytdlp_base_args",
        lambda: [
            "python",
            "-m",
            "yt_dlp",
            "--no-config",
            "--config-location",
            "yt-dlp.conf",
        ],
    )

    command = probe._production_command("https://youtu.be/example", tmp_path)
    joined = " ".join(map(str, command))

    assert "--config-location yt-dlp.conf" in joined
    assert "--abort-on-unavailable-fragments" in command
    assert "bestaudio/best" in command
    assert "--no-playlist" in command
    assert " 18 " not in f" {joined} "
    assert "--test" not in command


def test_download_uses_repo_owned_process_tree_runner(monkeypatch, tmp_path):
    import services.async_process as async_process

    captured: dict[str, object] = {}

    async def fake_runner(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(command, 0, "ok", "")

    monkeypatch.setattr(async_process, "run_cancellable_process", fake_runner)
    monkeypatch.setattr(
        probe,
        "_production_command",
        lambda _url, _root: ["python", "-m", "yt_dlp", "example"],
    )

    process = probe._run_download("https://youtu.be/example", tmp_path)

    assert process.returncode == 0
    assert captured["command"] == ["python", "-m", "yt_dlp", "example"]
    assert captured["kwargs"] == {
        "cwd": probe.PROJECT_ROOT,
        "timeout": 1800,
        "text": True,
    }


def test_direct_cli_help_can_import_repo_owned_services():
    process = subprocess.run(
        [sys.executable, "tools/check_youtube_gvs.py", "--help"],
        cwd=probe.PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )

    assert process.returncode == 0, process.stderr
    assert "bestaudio/best" in process.stdout


def test_failure_classifier_distinguishes_gvs_403_and_login():
    forbidden = subprocess.CompletedProcess(
        args=["yt-dlp"],
        returncode=1,
        stdout="",
        stderr="ERROR: unable to download video data: HTTP Error 403: Forbidden",
    )
    login = subprocess.CompletedProcess(
        args=["yt-dlp"],
        returncode=1,
        stdout="",
        stderr="ERROR: Sign in to confirm you're not a bot",
    )

    assert probe._classify_failure(forbidden) == "FAIL_HTTP_403"
    assert probe._classify_failure(login) == "FAIL_LOGIN_REQUIRED"


def test_success_requires_complete_media_and_ffprobe(monkeypatch, capsys):
    monkeypatch.setattr(probe, "_prepare_runtime", lambda: "source-only=on")
    monkeypatch.setattr(
        probe,
        "_run_download",
        lambda _url, _root: subprocess.CompletedProcess(
            args=["yt-dlp"], returncode=0, stdout="", stderr=""
        ),
    )

    def fake_media(root: Path) -> Path:
        path = root / "gvs_probe.webm"
        path.write_bytes(b"probe-media")
        return path

    monkeypatch.setattr(probe, "_find_downloaded_media", fake_media)
    monkeypatch.setattr(probe, "_ffprobe_duration", lambda _path: 123.456)

    assert probe.main(["https://youtu.be/example"]) == 0
    output = capsys.readouterr().out
    assert "GVS_ACCEPTANCE=PASS" in output
    assert "duration=123.456s" in output


def test_http_403_is_fail_closed(monkeypatch, capsys):
    monkeypatch.setattr(probe, "_prepare_runtime", lambda: "source-only=on")
    monkeypatch.setattr(
        probe,
        "_run_download",
        lambda _url, _root: subprocess.CompletedProcess(
            args=["yt-dlp"],
            returncode=1,
            stdout="",
            stderr="ERROR: unable to download video data: HTTP Error 403: Forbidden",
        ),
    )

    assert probe.main(["https://youtu.be/example"]) == 3
    assert "GVS_ACCEPTANCE=FAIL_HTTP_403" in capsys.readouterr().out


def test_download_timeout_is_fail_closed(monkeypatch, capsys):
    monkeypatch.setattr(probe, "_prepare_runtime", lambda: "source-only=on")

    def timeout(_url, _root):
        raise subprocess.TimeoutExpired(cmd=["yt-dlp"], timeout=1800)

    monkeypatch.setattr(probe, "_run_download", timeout)

    assert probe.main(["https://youtu.be/example"]) == 6
    assert "GVS_ACCEPTANCE=FAIL_TIMEOUT" in capsys.readouterr().out
