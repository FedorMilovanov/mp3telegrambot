from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from services.tts_weighted_runner_provisioning import (
    REQUIRED_ENVIRONMENT_KEYS,
    REQUIRED_RUNNER_LABELS,
    TTS_WEIGHTED_RUNNER_PROVISIONING_POLICY,
    WeightedTTSRunnerProvisioningConfig,
    run_weighted_tts_runner_provisioning_check,
)
from services.tts_weighted_smoke_runner import TTS_WEIGHTED_SMOKE_RUNNER_POLICY


REPOSITORY = "FedorMilovanov/mp3telegrambot"
PROFILE_ID = "voxcpm2-production-v1"


def _write_runner(
    root: Path,
    *,
    github_url: str = "https://github.com/FedorMilovanov/mp3telegrambot",
    ephemeral: bool = False,
) -> None:
    (root / "bin").mkdir(parents=True, exist_ok=True)
    for relative in ("config.cmd", "run.cmd", "bin/Runner.Listener.exe"):
        (root / relative).write_bytes(b"fixture")
    payload = {
        "agentId": 41,
        "agentName": "weighted-runner-01",
        "poolId": 1,
        "poolName": "Default",
        "ephemeral": ephemeral,
        "serverUrl": "https://pipelines.actions.githubusercontent.com/private",
        "gitHubUrl": github_url,
        "workFolder": "_work",
    }
    (root / ".runner").write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )
    (root / ".service").write_text(
        "actions.runner.FedorMilovanov-mp3telegrambot.weighted-runner-01.service\n",
        encoding="utf-8",
    )


def _config(tmp_path: Path, monkeypatch) -> WeightedTTSRunnerProvisioningConfig:
    runner = tmp_path / "private-runner"
    runner.mkdir()
    _write_runner(runner)
    python = tmp_path / "private-python.exe"
    python.write_bytes(b"python")
    model = tmp_path / "private-model"
    model.mkdir()
    (model / "config.json").write_text('{"model":"fixture"}', encoding="utf-8")
    reference = tmp_path / "private-reference.wav"
    reference.write_bytes(b"wave-fixture")
    work = tmp_path / "private-work"
    monkeypatch.setenv("TTS_SMOKE_PYTHON", str(python))
    monkeypatch.setenv("TTS_SMOKE_MODEL_ROOT", str(model))
    monkeypatch.setenv("TTS_SMOKE_REFERENCE_WAV", str(reference))
    return WeightedTTSRunnerProvisioningConfig(
        runner_directory=runner,
        repository=REPOSITORY,
        profile_id=PROFILE_ID,
        python_executable=python,
        model_directory=model,
        reference_wav=reference,
        work_directory=work,
    )


def _doctor_report(*, weights_loaded: bool = False) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "policy": TTS_WEIGHTED_SMOKE_RUNNER_POLICY,
        "report_policy": "privacy-safe-runner-doctor-report-v1",
        "passed": True,
        "profile": {
            "profile_id": PROFILE_ID,
            "backend_id": "voxcpm2",
            "model_revision": "openbmb-voxcpm2-pinned-v1",
            "profile_fingerprint": "a" * 64,
            "source": {"source_sha256": "b" * 64},
        },
        "backend": {"backend_id": "voxcpm2"},
        "model": {"config_present": True, "config_sha256": "c" * 64},
        "imports": {
            "modules": [
                {"name": "numpy", "version": "2"},
                {"name": "soundfile", "version": "0.13"},
                {"name": "torch", "version": "2"},
                {"name": "voxcpm", "version": "1"},
            ]
        },
        "ffprobe": {"available": True, "version": "7.1"},
        "environment": {
            "hf_hub_offline": True,
            "transformers_offline": True,
            "configured_threads": 6,
        },
        "storage": {
            "write": True,
            "fsync": True,
            "replace": True,
            "readback": True,
            "cleanup": True,
        },
        "runtime": {
            "python_version": "3.11.9",
            "platform_system": "Windows",
            "machine": "AMD64",
            "weights_loaded": weights_loaded,
            "session_opened": False,
        },
    }


def _run(
    config: WeightedTTSRunnerProvisioningConfig,
    *,
    report: dict[str, Any] | None = None,
    service_running: bool = True,
) -> dict[str, Any]:
    return run_weighted_tts_runner_provisioning_check(
        config,
        service_probe=lambda _name: service_running,
        doctor_runner=lambda _doctor_config: report or _doctor_report(),
    )


def test_provisioning_accepts_repository_runner_and_retains_only_safe_report(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config = _config(tmp_path, monkeypatch)

    report = _run(config)
    report_path = config.work_directory / "setup-report.json"
    serialized = report_path.read_text(encoding="utf-8")

    assert report["passed"] is True
    assert report["policy"] == TTS_WEIGHTED_RUNNER_PROVISIONING_POLICY
    assert report["required_labels"] == list(REQUIRED_RUNNER_LABELS)
    assert report["runner"]["registration_scope"] == "repository"
    assert report["runner"]["service_running"] is True
    assert report["environment"]["keys"] == list(REQUIRED_ENVIRONMENT_KEYS)
    assert report["doctor"]["weights_loaded"] is False
    assert report["doctor"]["session_opened"] is False
    assert report["doctor"]["runtime_modules"] == [
        "numpy",
        "soundfile",
        "torch",
        "voxcpm",
    ]
    assert report_path.is_file()
    assert not (config.work_directory / "doctor").exists()
    assert "weighted-runner-01" not in serialized
    assert "actions.runner." not in serialized
    for private in (
        config.runner_directory,
        config.python_executable,
        config.model_directory,
        config.reference_wav,
        config.work_directory,
    ):
        assert str(private) not in serialized
    assert '"path"' not in serialized
    assert "serverUrl" not in serialized


def test_provisioning_accepts_owner_organization_runner(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config = _config(tmp_path, monkeypatch)
    _write_runner(
        config.runner_directory,
        github_url="https://github.com/FedorMilovanov",
    )

    report = _run(config)

    assert report["runner"]["registration_scope"] == "organization"
    assert report["runner"]["repository_eligible"] is True


def test_provisioning_rejects_runner_registered_elsewhere(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config = _config(tmp_path, monkeypatch)
    _write_runner(
        config.runner_directory,
        github_url="https://github.com/other-owner/other-repo",
    )

    with pytest.raises(RuntimeError, match="другого GitHub"):
        _run(config)


def test_provisioning_rejects_ephemeral_or_stopped_service(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config = _config(tmp_path, monkeypatch)
    _write_runner(config.runner_directory, ephemeral=True)
    with pytest.raises(RuntimeError, match="persistent"):
        _run(config)

    _write_runner(config.runner_directory, ephemeral=False)
    with pytest.raises(RuntimeError, match="Running state"):
        _run(config, service_running=False)


def test_provisioning_rejects_duplicate_runner_json_keys(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config = _config(tmp_path, monkeypatch)
    (config.runner_directory / ".runner").write_text(
        '{"agentId":1,"agentId":2}',
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="строгим JSON"):
        _run(config)


def test_provisioning_rejects_environment_mismatch(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config = _config(tmp_path, monkeypatch)
    monkeypatch.setenv("TTS_SMOKE_MODEL_ROOT", str(tmp_path / "another-model"))

    with pytest.raises(RuntimeError, match="TTS_SMOKE_MODEL_ROOT"):
        _run(config)


def test_provisioning_rejects_doctor_that_loaded_weights(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config = _config(tmp_path, monkeypatch)

    with pytest.raises(RuntimeError, match="no-weights"):
        _run(config, report=_doctor_report(weights_loaded=True))
    assert not (config.work_directory / "setup-report.json").exists()
    assert not (config.work_directory / "doctor").exists()


def test_provisioning_rejects_nonempty_work_directory(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config = _config(tmp_path, monkeypatch)
    config.work_directory.mkdir()
    (config.work_directory / "foreign.txt").write_text("keep", encoding="utf-8")

    with pytest.raises(RuntimeError, match="пустой директорией"):
        _run(config)
    assert (config.work_directory / "foreign.txt").read_text(encoding="utf-8") == "keep"


def test_repository_normalization_rejects_unsafe_values(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        WeightedTTSRunnerProvisioningConfig(
            runner_directory=tmp_path,
            repository="owner/repo/extra",
            profile_id=PROFILE_ID,
            python_executable=tmp_path / "python.exe",
            model_directory=tmp_path,
            reference_wav=tmp_path / "reference.wav",
            work_directory=tmp_path / "work",
        )


def test_service_descriptor_does_not_allow_command_text(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config = _config(tmp_path, monkeypatch)
    (config.runner_directory / ".service").write_text(
        "actions.runner.owner;Remove-Item.repo.service\n",
        encoding="utf-8",
    )

    called = False

    def service_probe(_name: str) -> bool:
        nonlocal called
        called = True
        return True

    with pytest.raises(RuntimeError, match="запрещённые символы"):
        run_weighted_tts_runner_provisioning_check(
            config,
            service_probe=service_probe,
            doctor_runner=lambda _doctor_config: _doctor_report(),
        )
    assert called is False


def test_environment_is_restored_by_pytest_fixture(monkeypatch) -> None:
    for key in REQUIRED_ENVIRONMENT_KEYS:
        monkeypatch.delenv(key, raising=False)
    assert all(os.environ.get(key) is None for key in REQUIRED_ENVIRONMENT_KEYS)
