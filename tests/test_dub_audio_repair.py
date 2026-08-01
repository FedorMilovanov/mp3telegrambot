from __future__ import annotations

import json
from pathlib import Path

import pytest

from handlers.dub_audio_repair import _ensure_repair_slot, parse_segment_selector
from tools.voxcpm2 import dub_worker
from tools.voxcpm2.generic_audio_repair_runtime import (
    _source_cues,
    prepare_repair_checkpoints,
)
from tools.voxcpm2.generic_audio_repair_runtime_bootstrap import (
    build_recovered_manifest,
    ensure_repair_manifest,
)
from tools.voxcpm2.semantic_tts_guard_v4 import _GUARD_VERSION


def test_segment_selector_accepts_lists_ranges_and_all() -> None:
    available = [1, 2, 3, 4, 5]
    assert parse_segment_selector("2,4-5", available) == [2, 4, 5]
    assert parse_segment_selector("5-3", available) == [3, 4, 5]
    assert parse_segment_selector("все", available) == available
    with pytest.raises(ValueError, match="нет реплик"):
        parse_segment_selector("6", available)


def test_active_job_blocks_a_second_repair_request() -> None:
    class Store:
        @staticmethod
        def recent_jobs(project_id: str, limit: int = 8):
            assert project_id == "dub-test"
            assert limit == 8
            return [{"id": 17, "status": "running"}]

    with pytest.raises(RuntimeError, match="задание #17"):
        _ensure_repair_slot(Store(), "dub-test")


def test_finished_job_does_not_block_repair() -> None:
    class Store:
        @staticmethod
        def recent_jobs(project_id: str, limit: int = 8):
            return [{"id": 16, "status": "succeeded"}]

    _ensure_repair_slot(Store(), "dub-test")


def test_ready_srt_full_repair_recovers_source_cues_from_segments(
    tmp_path: Path,
) -> None:
    (tmp_path / "segments_ru_final.json").write_text(
        json.dumps(
            [
                {
                    "id": 1,
                    "start": 1.2,
                    "original_srt_start": 1.1,
                    "end": 3.4,
                    "source_end": 3.8,
                    "source": "Первая реплика.",
                },
                {
                    "id": 2,
                    "start": 4.0,
                    "end": 6.5,
                    "source_end": 6.8,
                    "text": "Вторая реплика.",
                },
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    cues = _source_cues(tmp_path)

    assert [(cue.start, cue.end, cue.text) for cue in cues] == [
        (1.1, 3.8, "Первая реплика."),
        (4.0, 6.8, "Вторая реплика."),
    ]


def test_source_groups_remain_preferred_for_gemini_projects(tmp_path: Path) -> None:
    (tmp_path / "source_groups.json").write_text(
        json.dumps(
            [{"id": 1, "start": 0.5, "end": 2.0, "source": "Original speech."}]
        ),
        encoding="utf-8",
    )
    (tmp_path / "segments_ru_final.json").write_text(
        json.dumps(
            [{"id": 1, "start": 9.0, "end": 10.0, "source": "Fallback."}]
        ),
        encoding="utf-8",
    )

    cues = _source_cues(tmp_path)

    assert [(cue.start, cue.end, cue.text) for cue in cues] == [
        (0.5, 2.0, "Original speech.")
    ]


def test_missing_manifest_is_recovered_without_translation_call(tmp_path: Path) -> None:
    (tmp_path / "source").mkdir()
    (tmp_path / "source" / "source.mp4").write_bytes(b"video")
    (tmp_path / "segments_ru_final.json").write_text(
        json.dumps([{"id": 1, "start": 0.0, "end": 2.0, "text": "Текст"}]),
        encoding="utf-8",
    )
    (tmp_path / "russian_title.txt").write_text(
        "Сила и Достоинство Благочестивой Женщины - Джон Пайпер\n",
        encoding="utf-8",
    )
    output = tmp_path / "output"
    output.mkdir()
    (output / "russian_subtitles.srt").write_text("1\n", encoding="utf-8")
    (output / "russian_translation.txt").write_text("Текст\n", encoding="utf-8")
    request = {
        "video_id": "z20py4yqhyq",
        "source_url": "https://youtu.be/z20py4yqhyq",
        "translation_mode": "gemini",
        "original_level": 0.18,
    }

    manifest = ensure_repair_manifest(tmp_path, request, "dub-330e0f949c")
    saved = json.loads((output / "manifest.json").read_text(encoding="utf-8"))

    assert manifest == saved
    assert saved["manifest_recovered"] is True
    assert saved["translation_reused"] is True
    assert saved["gemini_called"] is False
    assert saved["segments"] == 1
    assert saved["outputs"]["mixed"].endswith("— русский дубляж.mp4")
    assert len(saved["telegram_outputs"]) == 4


def test_manifest_recovery_rejects_project_without_finished_segments(
    tmp_path: Path,
) -> None:
    (tmp_path / "source").mkdir()
    (tmp_path / "source" / "source.mp4").write_bytes(b"video")

    with pytest.raises(RuntimeError, match="используйте /dubrun"):
        build_recovered_manifest(
            tmp_path,
            {"video_id": "z20py4yqhyq"},
            "dub-330e0f949c",
        )


def _checkpoint(root: Path, segment_id: int, seed: int = 100) -> Path:
    path = root / "checkpoints" / f"segment_{segment_id:02d}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"signature": {"base_seed": seed}, "report": {"id": segment_id}}),
        encoding="utf-8",
    )
    for directory in ("segments_clean", "segments_fitted", "attempts"):
        target = root / directory / f"{segment_id:02d}_sample.wav"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"wav")
    return path


def test_partial_repair_retargets_good_checkpoints_and_deletes_selected(
    tmp_path: Path,
) -> None:
    for segment_id in (1, 2, 3):
        _checkpoint(tmp_path, segment_id)
    (tmp_path / "semantic_guard.marker.json").write_text(
        json.dumps({"guard_version": _GUARD_VERSION, "base_seed": 100}),
        encoding="utf-8",
    )

    prepare_repair_checkpoints(
        tmp_path,
        all_ids={1, 2, 3},
        selected_ids={2},
        new_base_seed=200,
        repair_all=False,
    )

    for segment_id in (1, 3):
        payload = json.loads(
            (tmp_path / "checkpoints" / f"segment_{segment_id:02d}.json").read_text()
        )
        assert payload["signature"]["base_seed"] == 200
    assert not (tmp_path / "checkpoints" / "segment_02.json").exists()
    assert not list((tmp_path / "attempts").glob("02_*"))


def test_full_repair_invalidates_all_audio_checkpoints(tmp_path: Path) -> None:
    for segment_id in (1, 2):
        _checkpoint(tmp_path, segment_id)
    (tmp_path / "semantic_guard.marker.json").write_text("{}", encoding="utf-8")

    prepare_repair_checkpoints(
        tmp_path,
        all_ids={1, 2},
        selected_ids={1, 2},
        new_base_seed=300,
        repair_all=True,
    )

    assert not list((tmp_path / "checkpoints").glob("segment_*.json"))
    assert not (tmp_path / "semantic_guard.marker.json").exists()


def test_recipe_routes_audio_repair_through_clean_utility_without_gemini() -> None:
    command, spec = dub_worker.build_command("generic_short_v1", "repair_audio")

    assert spec["kind"] == "utility"
    assert spec["runner"] == "python_module"
    assert spec["module"] == "tools.voxcpm2.generic_clean_audio_repair_runtime"
    assert command[1:3] == ["-m", spec["module"]]
    assert "-Mode" not in command

    repair_source = Path("tools/voxcpm2/generic_audio_repair_runtime.py").read_text(
        encoding="utf-8"
    )
    clean_source = Path(
        "tools/voxcpm2/generic_clean_audio_repair_runtime/__init__.py"
    ).read_text(encoding="utf-8")
    assert "translate_groups_max" not in repair_source
    assert "gemini_json" not in repair_source
    assert "translate_groups_max" not in clean_source
    assert "gemini_json" not in clean_source
    assert '"gemini_called": False' in repair_source
