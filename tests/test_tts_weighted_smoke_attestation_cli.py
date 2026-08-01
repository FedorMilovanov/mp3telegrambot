from __future__ import annotations

import json
from pathlib import Path
import sys

import tools.build_tts_weighted_smoke_attestation as cli


COMMIT_SHA = "d" * 40


def _github_environment(monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_REPOSITORY", "FedorMilovanov/mp3telegrambot")
    monkeypatch.setenv("GITHUB_SHA", COMMIT_SHA)
    monkeypatch.setenv("GITHUB_REF", "refs/heads/main")
    monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")
    monkeypatch.setenv(
        "GITHUB_WORKFLOW_REF",
        "FedorMilovanov/mp3telegrambot/"
        ".github/workflows/tts-weighted-smoke.yml@refs/heads/main",
    )
    monkeypatch.setenv("GITHUB_RUN_ID", "30712648726")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")


def test_cli_uses_immutable_github_environment_and_writes_attestation(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    _github_environment(monkeypatch)
    doctor = tmp_path / "doctor" / "report.json"
    smoke = tmp_path / "smoke" / "report.json"
    output = tmp_path / "attestation" / "attestation.json"
    captured: dict = {}

    monkeypatch.setattr(
        cli,
        "load_weighted_tts_report",
        lambda path, label: {"label": label, "source": str(path.name)},
    )

    def fake_build(doctor_report, smoke_report, context, *, forbidden_values):
        captured["doctor"] = doctor_report
        captured["smoke"] = smoke_report
        captured["context"] = context
        captured["forbidden"] = forbidden_values
        return {
            "subject": {
                "commit_sha": context.commit_sha,
                "run_id": context.run_id,
                "run_attempt": context.run_attempt,
            },
            "result": {
                "profile_id": "voxcpm2-production-v1",
                "model_revision": "local-archive-pinned-v1",
            },
            "digest_sha256": "a" * 64,
        }

    def fake_write(path: Path, payload) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")

    monkeypatch.setattr(cli, "build_weighted_tts_attestation", fake_build)
    monkeypatch.setattr(cli, "write_weighted_tts_attestation", fake_write)
    monkeypatch.setattr(cli, "validate_weighted_tts_attestation", lambda *a, **k: None)

    code = cli.main(
        [
            "--doctor-report",
            str(doctor),
            "--smoke-report",
            str(smoke),
            "--output",
            str(output),
        ]
    )

    assert code == 0
    assert output.is_file()
    assert captured["context"].commit_sha == COMMIT_SHA
    assert captured["context"].run_id == 30712648726
    assert captured["context"].run_attempt == 2
    assert str(doctor.resolve()) in captured["forbidden"]
    assert str(smoke.resolve()) in captured["forbidden"]
    assert str(output.resolve()) in captured["forbidden"]
    stdout = capsys.readouterr().out
    assert "TTS_WEIGHTED_SMOKE_ATTESTATION_OK" in stdout
    assert COMMIT_SHA in stdout
    assert "digest=" + "a" * 64 in stdout


def test_cli_failure_redacts_source_output_python_and_repository_paths(
    tmp_path: Path,
) -> None:
    doctor = tmp_path / "private-doctor" / "report.json"
    smoke = tmp_path / "private-smoke" / "report.json"
    output = tmp_path / "private-attestation" / "attestation.json"
    raw = " | ".join(
        (
            str(doctor.resolve()),
            str(smoke.resolve()),
            str(output.resolve()),
            str(Path(sys.executable).resolve()),
            str(Path.cwd().resolve()),
        )
    )
    redacted = cli._redacted_failure(
        RuntimeError(raw),
        doctor_report=doctor,
        smoke_report=smoke,
        output=output,
    )

    assert str(doctor.resolve()) not in redacted
    assert str(smoke.resolve()) not in redacted
    assert str(output.resolve()) not in redacted
    assert str(Path(sys.executable).resolve()) not in redacted
    assert str(Path.cwd().resolve()) not in redacted
    assert "<DOCTOR_REPORT>" in redacted
    assert "<SMOKE_REPORT>" in redacted
    assert "<ATTESTATION>" in redacted
