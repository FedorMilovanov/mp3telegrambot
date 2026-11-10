from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools" / "voxcpm2" / "release_gate.py"


def _gate() -> Any:
    spec = importlib.util.spec_from_file_location("voxcpm2_release_gate", MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GATE = _gate()


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def case(tmp_path: Path) -> tuple[dict[str, Any], Path]:
    original = tmp_path / "original.mp4"
    edited = tmp_path / "edited.mp4"
    original.write_bytes(b"original mp4 bytes")
    edited.write_bytes(b"changed mp4 bytes")
    final_sha = _sha(edited)
    manifest = {
        "schema_version": 1,
        "target": "clip",
        "source": {"path": original.name, "sha256": _sha(original)},
        "artifact": {"path": edited.name, "sha256": final_sha},
        "changes": [{
            "issue_id": "PW-W15",
            "speaker": "Paul Washer",
            "start_s": 2625.31,
            "end_s": 2637.035,
            "method": "pcm_edit",
            "source_en": "Context of the original English line",
            "old_ru": "Кому вы подражаете? [unwanted sound]",
            "new_ru": "Кому вы подражаете? Всё равно, цитируете ли вы Джона Кальвина...",
            "left_word": "подражаете",
            "right_word": "Джона",
            "postmix_aac_listened": True,
            "asr_checked": True,
        }],
        "checks": {
            "source_streams_verified": True,
            "pcm_scope_verified": True,
            "postmix_asr_completed": True,
            "meaning_verified": True,
            "join_tone_verified": True,
        },
        "human_review": {
            "reviewer": "owner",
            "verdict": "APPROVED",
            "reviewed_at": "2026-11-10T22:17:00+03:00",
            "artifact_sha256": final_sha,
            "listened_from_final_mp4": True,
            "accepted_issue_ids": ["PW-W15"],
            "asr_disagreements_resolved_by_listening": True,
            "asr_resolution_note": "Owner accepted encoded MP4 fragment after comparison.",
        },
        "asr_disagreements": ["PW-W15"],
        "subtitle_status": "OPEN",
        "open_issues": ["Subtitles around 50:23-50:55 still need editorial recheck"],
        "publication": {
            "whole_film_listened": False,
            "rights_cleared": False,
            "publisher_approved": False,
        },
    }
    return manifest, tmp_path / "acceptance.json"


def _check(case: tuple[dict[str, Any], Path], target: str = "clip") -> dict[str, Any]:
    manifest, path = case
    return GATE.assess(manifest, path, target)


def test_owner_approved_clip_is_not_a_publication_clearance(case: tuple[dict[str, Any], Path]) -> None:
    result = _check(case)
    assert result["passed"]
    assert result["status"] == "CLIP_ACCEPTED_ONLY_NOT_RELEASE_READY"


def test_publication_refuses_unreviewed_subtitles_and_unresolved_defects(case: tuple[dict[str, Any], Path]) -> None:
    manifest, _ = case
    manifest["target"] = "publication"
    result = _check(case, "publication")
    assert not result["passed"]
    assert any("subtitles" in err for err in result["errors"])
    assert any("open_issues" in err for err in result["errors"])
    assert any("whole_film_listened" in err for err in result["errors"])


def test_publication_passes_only_with_complete_evidence(case: tuple[dict[str, Any], Path]) -> None:
    manifest, _ = case
    manifest["target"] = "publication"
    manifest["subtitle_status"] = "PASS"
    manifest["open_issues"] = []
    manifest["publication"] = {
        "whole_film_listened": True,
        "rights_cleared": True,
        "publisher_approved": True,
    }
    result = _check(case, "publication")
    assert result["passed"]
    assert result["status"] == "PUBLICATION_EVIDENCE_ACCEPTED"


@pytest.mark.parametrize(
    "value",
    [False, None, "yes"],
)
def test_human_audition_must_be_strict_true(case: tuple[dict[str, Any], Path], value: Any) -> None:
    case[0]["human_review"]["listened_from_final_mp4"] = value
    assert not _check(case)["passed"]


def test_reviewer_cannot_be_asr(case: tuple[dict[str, Any], Path]) -> None:
    case[0]["human_review"]["reviewer"] = "Whisper"
    assert not _check(case)["passed"]


def test_review_must_match_actual_file_hash(case: tuple[dict[str, Any], Path]) -> None:
    manifest, path = case
    (path.parent / "edited.mp4").write_bytes(b"mutated after review")
    assert any("file changed" in err for err in _check(case)["errors"])
    manifest["artifact"]["sha256"] = _sha(path.parent / "edited.mp4")
    assert any("human_review.artifact_sha256" in err for err in _check(case)["errors"])


def test_source_is_also_hash_checked(case: tuple[dict[str, Any], Path]) -> None:
    _, path = case
    (path.parent / "original.mp4").write_bytes(b"source corrupted")
    assert any("source.sha256" in err for err in _check(case)["errors"])


def test_missing_user_approval_blocks(case: tuple[dict[str, Any], Path]) -> None:
    case[0].pop("human_review")
    assert not _check(case)["passed"]


def test_acceptance_must_cover_all_edits(case: tuple[dict[str, Any], Path]) -> None:
    case[0]["human_review"]["accepted_issue_ids"] = []
    assert not _check(case)["passed"]


def test_bad_windows_are_not_accepted(case: tuple[dict[str, Any], Path]) -> None:
    case[0]["changes"][0]["start_s"] = True
    assert any("start_s/end_s" in err for err in _check(case)["errors"])


def test_overlapping_windows_block(case: tuple[dict[str, Any], Path]) -> None:
    second = dict(case[0]["changes"][0])
    second.update(issue_id="PW-W16", start_s=2630, end_s=2638)
    case[0]["changes"].append(second)
    case[0]["human_review"]["accepted_issue_ids"].append("PW-W16")
    assert any("overlapping" in err for err in _check(case)["errors"])


def test_unknown_or_open_asr_disagreement_blocks(case: tuple[dict[str, Any], Path]) -> None:
    case[0]["human_review"]["asr_disagreements_resolved_by_listening"] = False
    assert not _check(case)["passed"]
    case[0]["human_review"]["asr_disagreements_resolved_by_listening"] = True
    case[0]["asr_disagreements"] = ["OTHER"]
    assert not _check(case)["passed"]


def test_missing_boundary_context_blocks(case: tuple[dict[str, Any], Path]) -> None:
    case[0]["changes"][0]["right_word"] = ""
    assert not _check(case)["passed"]


def test_tts_needs_native_wave_hash(case: tuple[dict[str, Any], Path]) -> None:
    case[0]["changes"][0]["method"] = "tts"
    assert not _check(case)["passed"]
    case[0]["changes"][0]["native_wav_sha256"] = "a" * 64
    assert _check(case)["passed"]


def test_timestamp_needs_timezone(case: tuple[dict[str, Any], Path]) -> None:
    case[0]["human_review"]["reviewed_at"] = "2026-11-10T22:17:00"
    assert not _check(case)["passed"]


def test_target_must_match_manifest(case: tuple[dict[str, Any], Path]) -> None:
    assert not _check(case, "publication")["passed"]


def test_missing_source_or_same_path_blocks(case: tuple[dict[str, Any], Path]) -> None:
    manifest, _ = case
    manifest["source"] = dict(manifest["artifact"])
    assert not _check(case)["passed"]


def test_cli_exit_codes_and_report(case: tuple[dict[str, Any], Path]) -> None:
    manifest, path = case
    path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    report_path = path.parent / "report.json"
    passed = subprocess.run(
        [sys.executable, str(MODULE_PATH), str(path), "--target", "clip", "--report", str(report_path)],
        capture_output=True, text=True, check=False,
    )
    assert passed.returncode == 0
    assert json.loads(report_path.read_text(encoding="utf-8"))["passed"] is True
    denied = subprocess.run(
        [sys.executable, str(MODULE_PATH), str(path), "--target", "publication"],
        capture_output=True, text=True, check=False,
    )
    assert denied.returncode == 1
    assert json.loads(denied.stdout)["status"] == "BLOCKED"


def test_cli_bad_json_is_fail_closed(tmp_path: Path) -> None:
    file_path = tmp_path / "bad.json"
    file_path.write_text("{", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(MODULE_PATH), str(file_path), "--target", "clip"],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 1
    assert json.loads(result.stdout)["passed"] is False
