from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from services.tts_weighted_smoke_attestation import (
    TTS_WEIGHTED_SMOKE_ATTESTATION_POLICY,
    _digest_payload,
)
from tools.verify_tts_weighted_smoke_attestation import (
    verify_downloaded_attestation,
)


REPOSITORY = "FedorMilovanov/mp3telegrambot"
COMMIT = "d" * 40
RUN_ID = 30712648726
RUN_ATTEMPT = 1
PROFILE = "voxcpm2-production-v1"


def _attestation() -> dict:
    statement = {
        "schema_version": 1,
        "policy": TTS_WEIGHTED_SMOKE_ATTESTATION_POLICY,
        "subject": {
            "repository": REPOSITORY,
            "commit_sha": COMMIT,
            "ref": "refs/heads/main",
            "event_name": "workflow_dispatch",
            "workflow_ref": (
                f"{REPOSITORY}/.github/workflows/"
                "tts-weighted-smoke.yml@refs/heads/main"
            ),
            "run_id": RUN_ID,
            "run_attempt": RUN_ATTEMPT,
        },
        "result": {
            "passed": True,
            "doctor_weights_loaded": False,
            "doctor_session_opened": False,
            "audio_retained": False,
            "profile_id": PROFILE,
            "profile_fingerprint": "a" * 64,
            "source_sha256": "b" * 64,
            "model_config_present": True,
            "model_config_sha256": "c" * 64,
            "model_revision": "local-archive-pinned-v1",
            "backend_id": "voxcpm2",
            "output_sample_rate": 24_000,
            "output_duration_seconds": 4.0,
            "synthesis_seconds": 38.5,
            "total_seconds": 42.0,
        },
    }
    return {**statement, "digest_sha256": _digest_payload(statement)}


def _artifact(tmp_path: Path, payload: dict | None = None) -> Path:
    directory = tmp_path / "downloaded-attestation"
    directory.mkdir()
    (directory / "attestation.json").write_text(
        json.dumps(payload or _attestation(), ensure_ascii=False),
        encoding="utf-8",
    )
    return directory


def _verify(directory: Path) -> dict[str, str]:
    return verify_downloaded_attestation(
        directory,
        expected_repository=REPOSITORY,
        expected_commit=COMMIT,
        expected_run_id=RUN_ID,
        expected_run_attempt=RUN_ATTEMPT,
        expected_profile_id=PROFILE,
    )


def test_verifier_accepts_single_exact_artifact_and_returns_safe_outputs(
    tmp_path: Path,
) -> None:
    directory = _artifact(tmp_path)

    outputs = _verify(directory)

    assert outputs == {
        "attestation_digest": _attestation()["digest_sha256"],
        "commit_sha": COMMIT,
        "run_id": str(RUN_ID),
        "run_attempt": "1",
        "profile_id": PROFILE,
        "model_revision": "local-archive-pinned-v1",
        "backend_id": "voxcpm2",
        "output_duration_seconds": "4",
        "output_sample_rate": "24000",
        "audio_retained": "false",
    }
    serialized = json.dumps(outputs, sort_keys=True)
    assert str(directory) not in serialized


def test_verifier_rejects_extra_or_wrong_artifact_entries(tmp_path: Path) -> None:
    directory = _artifact(tmp_path)
    (directory / "raw-report.json").write_text("{}", encoding="utf-8")
    with pytest.raises(RuntimeError, match="ровно один entry"):
        _verify(directory)

    (directory / "raw-report.json").unlink()
    (directory / "attestation.json").rename(directory / "report.json")
    with pytest.raises(RuntimeError, match="неизвестный"):
        _verify(directory)


def test_verifier_rejects_digest_tamper(tmp_path: Path) -> None:
    payload = _attestation()
    payload["result"]["output_sample_rate"] = 48_000
    directory = _artifact(tmp_path, payload)

    with pytest.raises(ValueError, match="digest"):
        _verify(directory)


@pytest.mark.parametrize(
    ("keyword", "value", "match"),
    [
        ("expected_repository", "other/repo", "repository"),
        ("expected_commit", "e" * 40, "commit"),
        ("expected_run_id", RUN_ID + 1, "run identity"),
        ("expected_run_attempt", 2, "run identity"),
        ("expected_profile_id", "other-profile", "profile"),
    ],
)
def test_verifier_rejects_expected_identity_mismatch(
    tmp_path: Path,
    keyword: str,
    value,
    match: str,
) -> None:
    directory = _artifact(tmp_path)
    kwargs = {
        "expected_repository": REPOSITORY,
        "expected_commit": COMMIT,
        "expected_run_id": RUN_ID,
        "expected_run_attempt": RUN_ATTEMPT,
        "expected_profile_id": PROFILE,
    }
    kwargs[keyword] = value

    with pytest.raises(ValueError, match=match):
        verify_downloaded_attestation(directory, **kwargs)


def test_verifier_rejects_github_output_injection_even_with_valid_digest(
    tmp_path: Path,
) -> None:
    payload = _attestation()
    statement = copy.deepcopy(payload)
    statement.pop("digest_sha256")
    statement["result"]["model_revision"] = "safe\nunsafe=value"
    payload = {**statement, "digest_sha256": _digest_payload(statement)}
    directory = _artifact(tmp_path, payload)

    with pytest.raises(RuntimeError, match="Unsafe identifier output"):
        _verify(directory)


def test_verifier_rejects_markdown_delimiters_even_with_valid_digest(
    tmp_path: Path,
) -> None:
    payload = _attestation()
    statement = copy.deepcopy(payload)
    statement.pop("digest_sha256")
    statement["result"]["model_revision"] = "unsafe`revision"
    payload = {**statement, "digest_sha256": _digest_payload(statement)}
    directory = _artifact(tmp_path, payload)

    with pytest.raises(RuntimeError, match="Unsafe identifier output"):
        _verify(directory)


def test_verifier_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    directory = tmp_path / "downloaded-attestation"
    directory.mkdir()
    (directory / "attestation.json").write_text(
        '{"schema_version":1,"schema_version":1}',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="дублирующийся JSON key"):
        _verify(directory)
