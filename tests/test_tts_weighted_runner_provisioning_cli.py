from __future__ import annotations

from pathlib import Path

import pytest

import tools.check_tts_weighted_runner_provisioning as cli


def _argv(tmp_path: Path) -> list[str]:
    runner = tmp_path / "private-runner"
    runner.mkdir()
    python = tmp_path / "private-python.exe"
    python.write_bytes(b"python")
    model = tmp_path / "private-model"
    model.mkdir()
    reference = tmp_path / "private-reference.wav"
    reference.write_bytes(b"audio")
    return [
        "--runner-directory",
        str(runner),
        "--repository",
        "FedorMilovanov/mp3telegrambot",
        "--profile-id",
        "voxcpm2-production-v1",
        "--python-executable",
        str(python),
        "--model-directory",
        str(model),
        "--reference-wav",
        str(reference),
        "--work-directory",
        str(tmp_path / "private-work"),
    ]


def test_cli_prints_only_safe_success_summary(
    monkeypatch,
    capsys,
    tmp_path: Path,
) -> None:
    argv = _argv(tmp_path)

    def fake_run(config):
        config.work_directory.mkdir()
        (config.work_directory / "setup-report.json").write_text(
            '{"passed":true}',
            encoding="utf-8",
        )
        return {
            "runner": {"service_running": True},
            "doctor": {
                "profile_id": config.profile_id,
                "backend_id": "voxcpm2",
                "weights_loaded": False,
            },
        }

    monkeypatch.setattr(cli, "run_weighted_tts_runner_provisioning_check", fake_run)

    assert cli.main(argv) == 0
    captured = capsys.readouterr()
    assert "TTS_WEIGHTED_RUNNER_PROVISIONING_OK" in captured.out
    assert "service_running=True" in captured.out
    assert "weights_loaded=False" in captured.out
    assert "private-runner" not in captured.out
    assert "private-model" not in captured.out
    assert captured.err == ""


def test_cli_redacts_every_local_path_from_failure(
    monkeypatch,
    capsys,
    tmp_path: Path,
) -> None:
    argv = _argv(tmp_path)
    private_values = [
        str(tmp_path / "private-runner"),
        str(tmp_path / "private-python.exe"),
        str(tmp_path / "private-model"),
        str(tmp_path / "private-reference.wav"),
        str(tmp_path / "private-work"),
    ]

    def fake_run(_config):
        raise RuntimeError("failure " + " ".join(private_values))

    monkeypatch.setattr(cli, "run_weighted_tts_runner_provisioning_check", fake_run)

    assert cli.main(argv) == 1
    captured = capsys.readouterr()
    assert "TTS_WEIGHTED_RUNNER_PROVISIONING_FAILED RuntimeError" in captured.err
    for private in private_values:
        assert private not in captured.err
        assert private.replace("\\", "/") not in captured.err
    assert captured.out == ""


def test_cli_requires_explicit_runner_and_private_resources(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["--repository", "FedorMilovanov/mp3telegrambot"])
    assert exc_info.value.code == 2
