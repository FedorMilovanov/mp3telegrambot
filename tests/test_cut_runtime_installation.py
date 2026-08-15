from pathlib import Path

from services.runtime_manifest import DEFAULT_RUNTIME_FEATURES


def test_factory_execution_is_not_a_runtime_patch_feature():
    feature_ids = {item.feature_id for item in DEFAULT_RUNTIME_FEATURES}
    assert "shorts-factory-max" not in feature_ids

    manifest_source = Path("services/runtime_manifest.py").read_text(encoding="utf-8")
    assert "services.shorts_factory_runtime" not in manifest_source
    assert "install_shorts_factory_mode" not in manifest_source


def test_factory_quality_and_delivery_are_owned_by_source_modules():
    factory_source = Path("pipelines/shorts_factory.py").read_text(encoding="utf-8")
    short_delivery = Path("pipelines/factory_short_delivery.py").read_text(encoding="utf-8")
    source_owner = Path("services/shorts_factory_source.py").read_text(encoding="utf-8")
    quality_gate = Path("services/shorts_factory_quality_gate.py").read_text(encoding="utf-8")
    timing_source = Path("services/shorts_factory_timing.py").read_text(encoding="utf-8")

    assert "process_and_send_factory_shorts" in factory_source
    assert "public_max_seconds=FACTORY_LONG_PUBLIC_MAX_SEC" in factory_source
    assert "silence_snap_max_end=ceiling" in short_delivery
    assert "speed=1.0" in short_delivery
    assert "create_factory_plan_resumable" in source_owner
    assert "download_factory_audio_with_retry_cache" in source_owner
    assert "apply_factory_quality_gate" in source_owner
    assert "install_factory_plan_quality_gate" not in quality_gate
    assert "shorts_factory_runtime" not in factory_source
    assert "_TIMELINE_BY_VIDEO" not in timing_source
    assert "install_factory_ru_boundary_capture" not in timing_source


def test_required_runtime_import_failure_cannot_be_silently_ignored():
    manifest_source = Path("services/runtime_manifest.py").read_text(encoding="utf-8")
    assert "except Exception as exc:" in manifest_source
    assert "RuntimeBootstrapError" in manifest_source
    assert "Required runtime feature" in manifest_source
