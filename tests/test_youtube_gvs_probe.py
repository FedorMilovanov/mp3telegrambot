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
    assert "--print" in command
    assert "GVS_EXPECTED_DURATION=%(duration)s" in joined
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


def test_duration_parser_and_factory_tolerance_contract():
    stdout = "noise\nGVS_EXPECTED_DURATION=1000.0\n"
    assert probe._expected_duration_from_output(stdout) == 1000.0
    assert probe._duration_matches(1002.0, 1000.0)
    assert not probe._duration_matches(1002.1, 1000.0)
    assert probe._duration_matches(10014.9, 10000.0)
    assert not probe._duration_matches(10015.1, 10000.0)


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


def test_success_requires_complete_media_and_matching_duration(monkeypatch, capsys):
    monkeypatch.setattr(probe, "_prepare_runtime", lambda: "source-only=on")
    monkeypatch.setattr(
        probe,
        "_run_download",
        lambda _url, _root: subprocess.CompletedProcess(
            args=["yt-dlp"],
            returncode=0,
            stdout="GVS_EXPECTED_DURATION=123.0\n",
            stderr="",
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
    assert "metadata=123.000s" in output
    assert "ffprobe=123.456s" in output


def test_success_without_metadata_duration_fails_closed(monkeypatch, capsys):
    monkeypatch.setattr(probe, "_prepare_runtime", lambda: "source-only=on")
    monkeypatch.setattr(
        probe,
        "_run_download",
        lambda _url, _root: subprocess.CompletedProcess(
            args=["yt-dlp"], returncode=0, stdout="", stderr=""
        ),
    )

    assert probe.main(["https://youtu.be/example"]) == 7
    assert "GVS_ACCEPTANCE=FAIL_METADATA_DURATION" in capsys.readouterr().out


def test_duration_mismatch_is_fail_closed(monkeypatch, capsys):
    monkeypatch.setattr(probe, "_prepare_runtime", lambda: "source-only=on")
    monkeypatch.setattr(
        probe,
        "_run_download",
        lambda _url, _root: subprocess.CompletedProcess(
            args=["yt-dlp"],
            returncode=0,
            stdout="GVS_EXPECTED_DURATION=100.0\n",
            stderr="",
        ),
    )

    def fake_media(root: Path) -> Path:
        path = root / "gvs_probe.webm"
        path.write_bytes(b"probe-media")
        return path

    monkeypatch.setattr(probe, "_find_downloaded_media", fake_media)
    monkeypatch.setattr(probe, "_ffprobe_duration", lambda _path: 80.0)

    assert probe.main(["https://youtu.be/example"]) == 9
    assert "GVS_ACCEPTANCE=FAIL_DURATION_MISMATCH" in capsys.readouterr().out


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
