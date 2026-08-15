from __future__ import annotations

import inspect
from pathlib import Path

import services.shorts_factory_source as factory_source
import services.translation_editorial_runner as runner


def test_editorial_runner_has_no_audio_ordering_marker_or_job_context():
    source = inspect.getsource(runner)
    assert "mark_factory_analysis_audio_skipped" not in source
    assert "JOB_STATE" not in source
    assert "ContextVar" not in source
    assert "translation_editorial_pending" not in source


def test_editorial_runner_passes_known_duration_into_source_owned_translation():
    source = inspect.getsource(runner.process_translation_editorial_only)
    assert "prepare_factory_translation_video(url, workdir, duration, \"en\")" in source
    assert "factory_preflight_issues(" in source
    assert "enforce_factory_translation_preflight()" in source


def test_translation_source_owns_video_capacity_proof_before_download():
    source = inspect.getsource(factory_source.prepare_factory_translation_video)
    ensure_pos = source.index("ensure_factory_video_space(")
    original_task_pos = source.index("download_factory_video_source(")
    assert ensure_pos < original_task_pos
    assert "duration_seconds=float(duration)" in source
    assert "expected_duration=float(duration)" in source


def test_factory_pipeline_builds_editorial_pack_directly_without_handoff_bridge():
    source = Path("pipelines/shorts_factory.py").read_text(encoding="utf-8")
    assert "prepare_factory_editorial_review(" in source
    assert "send_factory_editorial_files(" in source
    assert "shorts_factory_editorial_bridge" not in source
    assert "editorial_source" not in source


def test_translation_editorial_runner_is_the_only_standalone_owner():
    dispatcher = Path("pipelines/video_dispatch.py").read_text(encoding="utf-8")
    routing_bridge = Path(
        "services/shorts_factory_overload_editorial_polish.py"
    ).read_text(encoding="utf-8")
    assert "services.translation_editorial_runner" in dispatcher
    assert "services.translation_editorial_runner" in routing_bridge
    assert "shorts_factory_editorial_bridge" not in dispatcher
    assert "shorts_factory_editorial_bridge" not in routing_bridge
