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
    no_downgrade_source = Path(
        "services/shorts_factory_no_downgrade.py"
    ).read_text(encoding="utf-8")
    source_quality_source = Path(
        "services/shorts_factory_source.py"
    ).read_text(encoding="utf-8")
    disk_guard_source = Path(
        "services/shorts_factory_disk_guard.py"
    ).read_text(encoding="utf-8")
    timing_source = Path("services/shorts_factory_timing.py").read_text(
        encoding="utf-8"
    )

    assert "from services.shorts_factory_media import (" in runtime_source
    assert "validated_factory_source_duration" in runtime_source
    assert (
        "from services.shorts_factory_timing import "
        "align_factory_livedub_candidates"
    ) in runtime_source
    assert "\ninstall_livedub_downstream_media_policy()\n" not in media_source
    assert "\ninstall_factory_plan_quality_gate()\n" not in quality_source
    assert "\ninstall_factory_no_downgrade_policy()\n" not in no_downgrade_source
    assert (
        "\ninstall_factory_source_quality_policy()\n"
        not in source_quality_source
    )
    assert "\ninstall_factory_disk_guard()\n" not in disk_guard_source
    assert "\ninstall_livedub_downstream_media_policy()\n" not in timing_source
    assert "\ninstall_factory_plan_quality_gate()\n" not in timing_source
    assert "if not install_livedub_downstream_media_policy():" in runtime_source
    assert "if not install_factory_plan_quality_gate():" in runtime_source
    assert "if not install_factory_source_quality_policy():" in quality_source
    assert "if not install_factory_disk_guard():" in quality_source
    assert "if not install_factory_no_downgrade_policy():" in quality_source

    no_downgrade_pos = quality_source.index(
        "if not install_factory_no_downgrade_policy():"
    )
    source_pos = quality_source.index(
        "if not install_factory_source_quality_policy():"
    )
    disk_pos = quality_source.index("if not install_factory_disk_guard():")
    execution_pos = quality_source.index(
        "if not install_shorts_factory_execution_guard():"
    )
    assert no_downgrade_pos < source_pos < disk_pos < execution_pos


def test_required_factory_import_failure_cannot_be_silently_ignored():
    manifest_source = Path("services/runtime_manifest.py").read_text(
        encoding="utf-8"
    )

    assert "except Exception as exc:" in manifest_source
    assert "RuntimeBootstrapError" in manifest_source
    assert "Required runtime feature" in manifest_source
