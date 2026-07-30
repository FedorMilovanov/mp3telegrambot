from __future__ import annotations

from tools.voxcpm2 import clean_runtime_contract as contract


def test_render_contract_fingerprints_every_delivery_policy_module() -> None:
    required = {
        "tools/voxcpm2/direct_russian_cadence.py",
        "tools/voxcpm2/direct_tail_artifact.py",
        "tools/voxcpm2/direct_source_prosody.py",
        "tools/voxcpm2/direct_timeline_delivery_qa.py",
        "tools/voxcpm2/direct_max_quality_render.py",
    }

    assert required <= set(contract._RENDER_MODULES)


def test_release_contract_fingerprints_expression_aware_gate() -> None:
    assert (
        "tools/voxcpm2/clean_expression_aware_qa.py"
        in contract._RELEASE_MODULES
    )
