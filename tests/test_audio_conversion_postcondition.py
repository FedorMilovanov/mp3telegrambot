from pathlib import Path

from services import project_runtime_hardening as hardening


def test_zero_exit_without_valid_output_becomes_failure(tmp_path, monkeypatch) -> None:
    source = tmp_path / "video.mp3"
    output = tmp_path / "video_64.mp3"
    source.write_bytes(b"source" * 3000)
    command = ["ffmpeg", "-i", str(source), "-b:a", "64k", "-y", str(output)]

    def fake_run(cmd, *args, **kwargs):
        Path(cmd[-1]).write_bytes(b"invalid" * 3000)
        return hardening._subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(hardening._subprocess, "run", fake_run)
    monkeypatch.setattr(hardening, "_ffprobe_audio_ok", lambda _path: False)
    result = hardening._SubprocessProxy().run(command, capture_output=True)
    assert result.returncode != 0
    assert b"valid output" in result.stderr
    assert not output.exists()
