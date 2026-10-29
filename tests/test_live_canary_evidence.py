from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import sys
from typing import Any, cast

import pytest

from tools.verify_live_canary_evidence import (
    MAX_EVIDENCE_BYTES,
    MAX_LARGE_MEDIA_BYTES,
    MIN_LARGE_MEDIA_BYTES,
    REQUIRED_BOUNDARIES,
    LiveCanaryEvidenceError,
    build_evidence_template,
    load_evidence,
    main,
    validate_evidence,
)

SHA = "798268dbe43e9fcd332ea94c949bbe02509820e1"
OTHER_SHA = "a" * 40
PROOF = "b" * 64
ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "verify_live_canary_evidence.py"


def _valid_payload(source_kind: str = "operator_canary") -> dict[str, Any]:
    return {
        "schema": "mp3bot-live-canary-evidence-v1",
        "repository": "FedorMilovanov/mp3telegrambot",
        "commit_sha": SHA,
        "source_kind": source_kind,
        "started_at": "2026-10-29T15:00:00+03:00",
        "completed_at": "2026-10-29T16:00:00+03:00",
        "runtime": {"build_sha": SHA, "dirty": "no"},
        "boundaries": [
            {
                "id": boundary,
                "result": "passed",
                "observed_at": f"2026-10-29T15:{index + 1:02d}:00+03:00",
                "proof_sha256": f"{index + 1:064x}",
            }
            for index, boundary in enumerate(REQUIRED_BOUNDARIES)
        ],
        "large_media": {
            "result": "passed",
            "observed_at": "2026-10-29T15:30:00+03:00",
            "size_bytes": MIN_LARGE_MEDIA_BYTES + 1,
            "delivery_route": "local_bot_api",
            "proof_sha256": PROOF,
        },
        "sanitization": {
            "policy": "sanitized-no-secrets-no-user-media-v1",
            "secrets_removed": True,
            "user_media_removed": True,
            "identifiers_removed": True,
        },
    }


def _reject(payload: dict[str, Any], match: str, *, expected_sha: str = SHA) -> None:
    with pytest.raises(LiveCanaryEvidenceError, match=match):
        validate_evidence(payload, expected_sha=expected_sha)


@pytest.mark.parametrize("source_kind", ["operator_canary", "production_telemetry"])
def test_complete_sanitized_current_sha_evidence_is_accepted(source_kind: str) -> None:
    validate_evidence(_valid_payload(source_kind), expected_sha=SHA)


def test_template_is_explicitly_incomplete_and_cannot_close_the_audit() -> None:
    template = build_evidence_template(SHA)

    boundaries = cast(list[dict[str, object]], template["boundaries"])
    assert template["commit_sha"] == SHA
    assert template["runtime"] == {"build_sha": SHA, "dirty": "unknown"}
    assert [item["result"] for item in boundaries] == ["pending"] * len(REQUIRED_BOUNDARIES)
    with pytest.raises(LiveCanaryEvidenceError, match="started_at"):
        validate_evidence(template, expected_sha=SHA)


@pytest.mark.parametrize("boundary", REQUIRED_BOUNDARIES)
def test_every_live_boundary_is_mandatory(boundary: str) -> None:
    payload = _valid_payload()
    payload["boundaries"] = [item for item in payload["boundaries"] if item["id"] != boundary]

    _reject(payload, f"missing live boundaries:.*{boundary}")


def test_duplicate_boundary_is_rejected_instead_of_replacing_a_witness() -> None:
    payload = _valid_payload()
    payload["boundaries"][1]["id"] = payload["boundaries"][0]["id"]

    _reject(payload, "duplicate live boundary")


@pytest.mark.parametrize(
    ("key", "value", "match"),
    [
        ("schema", "other-schema", "evidence.schema"),
        ("repository", "example/other", "evidence.repository"),
        ("source_kind", "synthetic_test", "source_kind"),
    ],
)
def test_identity_and_source_kind_are_fixed(key: str, value: str, match: str) -> None:
    payload = _valid_payload()
    payload[key] = value

    _reject(payload, match)


def test_unexpected_free_form_field_is_rejected() -> None:
    payload = _valid_payload()
    payload["raw_log"] = "token-and-user-media"

    _reject(payload, "unknown=raw_log")


def test_non_string_object_keys_fail_cleanly_for_direct_api_callers() -> None:
    with pytest.raises(LiveCanaryEvidenceError, match="only string keys"):
        validate_evidence({1: "not-json"}, expected_sha=SHA)


@pytest.mark.parametrize("location", ["evidence", "runtime"])
def test_runtime_and_document_must_match_the_exact_expected_sha(location: str) -> None:
    payload = _valid_payload()
    if location == "evidence":
        payload["commit_sha"] = OTHER_SHA
    else:
        payload["runtime"]["build_sha"] = OTHER_SHA

    _reject(payload, "does not match|match the required")


@pytest.mark.parametrize("dirty", ["yes", "unknown"])
def test_dirty_or_unproven_runtime_tree_cannot_witness_exact_sha(dirty: str) -> None:
    payload = _valid_payload()
    payload["runtime"]["dirty"] = dirty

    _reject(payload, "must be clean")


def test_expected_sha_is_validated_before_evidence() -> None:
    _reject(_valid_payload(), "expected_sha", expected_sha="not-a-sha")


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("started_at", "2026-10-29T15:00:00", "timezone"),
        ("started_at", "not-a-time", "ISO-8601"),
        ("completed_at", "2026-10-29T14:59:59+03:00", "must not precede"),
    ],
)
def test_run_timestamps_are_aware_and_ordered(field: str, value: str, match: str) -> None:
    payload = _valid_payload()
    payload[field] = value

    _reject(payload, match)


def test_boundary_timestamp_must_be_inside_the_declared_run() -> None:
    payload = _valid_payload()
    payload["boundaries"][0]["observed_at"] = "2026-10-29T16:00:01+03:00"

    _reject(payload, "inside the canary")


def test_pending_boundary_is_not_a_passed_witness() -> None:
    payload = _valid_payload()
    payload["boundaries"][3]["result"] = "pending"

    _reject(payload, "result")


def test_invalid_proof_digest_is_rejected() -> None:
    payload = _valid_payload()
    payload["boundaries"][0]["proof_sha256"] = "not-a-digest"

    _reject(payload, "64 hexadecimal")


@pytest.mark.parametrize(
    ("size", "match"),
    [
        (MIN_LARGE_MEDIA_BYTES, "larger than 50 MiB"),
        (MAX_LARGE_MEDIA_BYTES + 1, "larger than 50 MiB"),
        (True, "must be an integer"),
        (float(MIN_LARGE_MEDIA_BYTES + 1), "must be an integer"),
    ],
)
def test_large_media_must_be_a_real_local_only_size(size: object, match: str) -> None:
    payload = _valid_payload()
    payload["large_media"]["size_bytes"] = size

    _reject(payload, match)


def test_cloud_delivery_cannot_prove_the_local_large_media_boundary() -> None:
    payload = _valid_payload()
    payload["large_media"]["delivery_route"] = "cloud"

    _reject(payload, "delivery_route")


@pytest.mark.parametrize("key", ["secrets_removed", "user_media_removed", "identifiers_removed"])
def test_sanitization_flags_are_mandatory(key: str) -> None:
    payload = _valid_payload()
    payload["sanitization"][key] = False

    _reject(payload, f"{key} must be true")


def test_sanitization_policy_cannot_be_relabelled() -> None:
    payload = _valid_payload()
    payload["sanitization"]["policy"] = "trust-me"

    _reject(payload, "unsupported sanitization policy")


def test_cli_accepts_complete_evidence_without_printing_proof_artifacts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    evidence = tmp_path / "evidence.json"
    evidence.write_text(json.dumps(_valid_payload()), encoding="utf-8")

    assert main([str(evidence), "--expected-sha", SHA]) == 0
    captured = capsys.readouterr()
    assert "evidence structure OK" in captured.out
    assert "independent review" in captured.out
    assert PROOF not in captured.out
    assert captured.err == ""


def test_standalone_template_command_runs_outside_the_repository(tmp_path: Path) -> None:
    completed = subprocess.run(
        [sys.executable, str(TOOL), "--expected-sha", SHA, "--template"],
        cwd=tmp_path,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["commit_sha"] == SHA


@pytest.mark.parametrize(
    ("content", "match"),
    [
        ("{", "invalid evidence JSON"),
        ('{"schema":"first","schema":"second"}', "duplicate JSON key"),
        ('{"size":NaN}', "non-finite JSON number"),
    ],
)
def test_cli_rejects_ambiguous_or_invalid_json(tmp_path: Path, content: str, match: str) -> None:
    evidence = tmp_path / "evidence.json"
    evidence.write_text(content, encoding="utf-8")

    assert main([str(evidence), "--expected-sha", SHA]) == 1


def test_excessive_json_nesting_is_cleanly_rejected_on_any_interpreter(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    evidence = tmp_path / "evidence.json"
    evidence.write_text("[" * 1100 + "]" * 1100, encoding="utf-8")

    assert main([str(evidence), "--expected-sha", SHA]) == 1
    assert "live canary evidence rejected" in capsys.readouterr().err


def test_decoder_recursion_error_has_a_stable_diagnostic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    evidence = tmp_path / "evidence.json"
    evidence.write_text("[]", encoding="utf-8")

    def fail_with_recursion_error(*args: Any, **kwargs: Any) -> object:
        raise RecursionError("decoder limit")

    monkeypatch.setattr("tools.verify_live_canary_evidence.json.loads", fail_with_recursion_error)
    with pytest.raises(LiveCanaryEvidenceError, match="nested too deeply"):
        load_evidence(evidence)


def test_invalid_utf8_is_a_clean_validation_failure(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    evidence = tmp_path / "evidence.json"
    evidence.write_bytes(b"\xff")

    assert main([str(evidence), "--expected-sha", SHA]) == 1
    assert "live canary evidence rejected" in capsys.readouterr().err


@pytest.mark.parametrize("content", [b"", b" " * (MAX_EVIDENCE_BYTES + 1)])
def test_empty_and_oversized_evidence_are_rejected(tmp_path: Path, content: bytes) -> None:
    evidence = tmp_path / "evidence.json"
    evidence.write_bytes(content)

    with pytest.raises(LiveCanaryEvidenceError, match="64 KiB"):
        load_evidence(evidence)


def test_missing_evidence_file_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(LiveCanaryEvidenceError, match="64 KiB"):
        load_evidence(tmp_path / "missing.json")


def test_validation_does_not_mutate_operator_evidence() -> None:
    payload = _valid_payload()
    before = copy.deepcopy(payload)

    validate_evidence(payload, expected_sha=SHA)

    assert payload == before
