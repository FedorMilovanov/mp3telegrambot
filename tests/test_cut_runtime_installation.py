from pathlib import Path

from services.runtime_manifest import DEFAULT_RUNTIME_FEATURES, RuntimePhase


def test_factory_required_feature_owns_all_cut_safety_policies():
    feature = next(
        item
        for item in DEFAULT_RUNTIME_FEATURES
        if item.feature_id == "shorts-factory-max"
    )

    assert feature.phase is RuntimePhase.POST_MAIN
    assert feature.required is True
    assert feature.requires_main is True
    assert feature.false_is_failure is True

    runtime_source = Path("services/shorts_factory_runtime.py").read_text(
        encoding="utf-8"
    )
    media_source = Path("services/shorts_factory_media.py").read_text(
        encoding="utf-8"
    )
    quality_source = Path("services/shorts_factory_quality_gate.py").read_text(
        encoding="utf-8"
    )
    timing_source = Path("services/shorts_factory_timing.py").read_text(
        encoding="utf-8"
    )

    assert (
        "from services.shorts_factory_media import "
        "validated_factory_source_duration"
    ) in runtime_source
    assert (
        "from services.shorts_factory_timing import "
        "align_factory_livedub_candidates"
    ) in runtime_source
    assert media_source.count("install_livedub_downstream_media_policy()") == 0
    assert quality_source.count("install_factory_plan_quality_gate()") == 0
    assert "install_livedub_downstream_media_policy()" in timing_source
    assert "install_factory_plan_quality_gate()" in timing_source


def test_required_factory_import_failure_cannot_be_silently_ignored():
    manifest_source = Path("services/runtime_manifest.py").read_text(
        encoding="utf-8"
    )

    assert "except Exception as exc:" in manifest_source
    assert "RuntimeBootstrapError" in manifest_source
    assert "Required runtime feature" in manifest_source
