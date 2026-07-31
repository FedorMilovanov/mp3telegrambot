from __future__ import annotations

from pathlib import Path

import pytest

from tools.voxcpm2 import clean_production_core as clean
from tools.voxcpm2 import clean_runtime_contract as contract
from tools.voxcpm2 import generic_clean_audio_repair_runtime as repair


def _fingerprints() -> dict[str, str]:
    return {
        "render_contract_sha256": "render-current",
        "release_contract_sha256": "release-current",
    }


def _marker(**changes):
    value = {
        "policy": clean.POLICY,
        "runtime_contract_policy": contract.POLICY,
        "render_contract_sha256": "render-current",
        "release_contract_sha256": "release-current",
        "segment_qa_passed": True,
        "release_complete": True,
        "base_seed": 100,
    }
    value.update(changes)
    return value


def test_selective_repair_accepts_only_release_complete_marker() -> None:
    passed, detail = repair._fingerprinted_baseline_ready(_marker(), _fingerprints())
    assert passed is True
    assert "release-complete" in detail

    passed, detail = repair._fingerprinted_baseline_ready(
        _marker(release_complete=False),
        _fingerprints(),
    )
    assert passed is False
    assert "AAC release baseline" in detail


def test_selective_repair_rejects_stale_render_or_release_fingerprint() -> None:
    passed, detail = repair._fingerprinted_baseline_ready(
        _marker(render_contract_sha256="old"),
        _fingerprints(),
    )
    assert passed is False
    assert "renderer/model/voxcpm" in detail

    passed, detail = repair._fingerprinted_baseline_ready(
        _marker(release_contract_sha256="old"),
        _fingerprints(),
    )
    assert passed is False
    assert "release fingerprint" in detail


def test_repair_seed_reserves_space_for_renderer_retry() -> None:
    marker = {"base_seed": 100}
    seed = repair._next_seed(
        {"base_seed": 10},
        marker,
        {"audio_repairs": []},
    )
    assert seed == 100 + contract.RETRY_SEED_OFFSET
    assert seed <= contract.MAX_BASE_SEED


def test_repair_seed_overflow_is_rejected() -> None:
    with pytest.raises(RuntimeError, match="безопасный диапазон"):
        repair._next_seed(
            {"base_seed": contract.MAX_BASE_SEED},
            {"base_seed": contract.MAX_BASE_SEED},
            {"audio_repairs": []},
        )


def test_clean_core_marks_qa_before_release_completion() -> None:
    source = Path(clean._legacy.__file__).read_text(encoding="utf-8")
    false_position = source.index('"release_complete": False')
    master_position = source.index("backend.build_master_command(")
    true_position = source.index("release_complete=True")
    assert false_position < master_position < true_position
    assert 'final_verification.get("passed") is not True' in source
    assert "Master не создал final_media_verification.json" in source
