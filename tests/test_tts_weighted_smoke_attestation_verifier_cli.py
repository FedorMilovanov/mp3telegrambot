from __future__ import annotations

import json
from pathlib import Path

import tools.verify_tts_weighted_smoke_attestation as cli


REPOSITORY = "FedorMilovanov/mp3telegrambot"
COMMIT = "d" * 40
RUN_ID = 30712648726
PROFILE = "voxcpm2-production-v1"


def _argv(tmp_path: Path, *, github_output: Path | None = None) -> list[str]:
    artifact = tmp_path / "private-artifact"
    artifact.mkdir()
    (artifact / "attestation.json").write_text("{}", encoding="utf-8")
    args = [
        "--artifact-directory",
        str(artifact),
        "--expected-repository",
        REPOSITORY,
        "--expected-commit",
        COMMIT,
        "--expected-run-id",
        str(RUN_ID),
        "--expected-run-attempt",
        "1",
        "--expected-profile-id",
        PROFILE,
    ]
    if github_output is not None:
        args.extend(["--github-output", str(github_output)])
    return args


def _outputs() -> dict[str, str]:
    return {
        "attestation_digest": "a" * 64,
        "commit_sha": COMMIT,
        "run_id": str(RUN_ID),
        "run_attempt": "1",
        "profile_id": PROFILE,
        "model_revision": "local-archive-pinned-v1",
        "backend_id": "voxcpm2",
        "output_duration_seconds": "4.0",
        "output_sample_rate": "24000",
        "audio_retained": "false",
    }


def test_cli_writes_fixed_github_outputs_and_safe_summary(
    monkeypatch,
    capsys,
    tmp_path: Path,
) -> None:
    github_output = tmp_path / "github-output.txt"
    github_output.write_text("existing=value\n", encoding="utf-8")
    monkeypatch.setattr(cli, "verify_downloaded_attestation", lambda *_a, **_k: _outputs())

    assert cli.main(_argv(tmp_path, github_output=github_output)) == 0

    captured = capsys.readouterr()
    assert "TTS_WEIGHTED_SMOKE_ATTESTATION_VERIFIED" in captured.out
    assert str(tmp_path) not in captured.out
    assert captured.err == ""
    lines = github_output.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "existing=value"
    assert lines[1:] == [f"{key}={value}" for key, value in _outputs().items()]


def test_cli_redacts_artifact_path_from_failure(
    monkeypatch,
    capsys,
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "private-artifact"

    def fail(*_args, **_kwargs):
        raise RuntimeError(f"failure at {artifact}")

    monkeypatch.setattr(cli, "verify_downloaded_attestation", fail)

    assert cli.main(_argv(tmp_path)) == 1
    captured = capsys.readouterr()
    assert "TTS_WEIGHTED_SMOKE_ATTESTATION_VERIFY_FAILED RuntimeError" in captured.err
    assert str(artifact) not in captured.err
    assert "<ARTIFACT_DIRECTORY>" in captured.err
    assert captured.out == ""


def test_cli_output_contract_contains_no_json_or_path_values(
    monkeypatch,
    capsys,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(cli, "verify_downloaded_attestation", lambda *_a, **_k: _outputs())

    assert cli.main(_argv(tmp_path)) == 0
    captured = capsys.readouterr()
    assert json.dumps(_outputs()) not in captured.out
    assert "private-artifact" not in captured.out
