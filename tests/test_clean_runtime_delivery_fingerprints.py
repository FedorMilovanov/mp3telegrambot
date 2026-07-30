from __future__ import annotations

from pathlib import Path

from tools.voxcpm2 import clean_runtime_contract as contract


ROOT = Path(__file__).resolve().parents[1]


def test_render_contract_fingerprints_every_delivery_policy_module() -> None:
    required = {
        "tools/voxcpm2/direct_max_quality_analysis/__init__.py",
        "tools/voxcpm2/direct_max_quality_render/__init__.py",
        "tools/voxcpm2/direct_retry_epoch.py",
        "tools/voxcpm2/direct_russian_cadence.py",
        "tools/voxcpm2/direct_russian_cadence/__init__.py",
        "tools/voxcpm2/direct_tail_artifact.py",
        "tools/voxcpm2/direct_source_prosody.py",
        "tools/voxcpm2/direct_timeline_delivery_qa.py",
        "tools/voxcpm2/direct_max_quality_render.py",
    }

    assert required <= set(contract._RENDER_MODULES)


def test_release_contract_fingerprints_active_final_delivery_gates() -> None:
    required = {
        "tools/voxcpm2/professional_audio_qa_v45.py",
        "tools/voxcpm2/final_encoded_delivery_qa.py",
    }

    assert required <= set(contract._RELEASE_MODULES)
    assert "tools/voxcpm2/clean_expression_aware_qa.py" not in contract._RELEASE_MODULES


def test_every_runtime_fingerprint_path_exists() -> None:
    names = tuple(
        dict.fromkeys((*contract._RENDER_MODULES, *contract._RELEASE_MODULES))
    )
    missing = [name for name in names if not (ROOT / name).is_file()]

    assert missing == []
