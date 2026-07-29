from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.voxcpm2 import clean_segment_normalizer as normalizer
from tools.voxcpm2 import legacy_segment_migration_v45 as migration


def _project(tmp_path: Path, segments) -> tuple[Path, Path, bytes]:
    root = tmp_path / "project"
    (root / "input").mkdir(parents=True)
    (root / "output").mkdir(parents=True)
    segments_path = root / "segments_ru_final.json"
    segments_path.write_text(
        json.dumps(segments, ensure_ascii=False, allow_nan=True),
        encoding="utf-8",
    )
    (root / "input" / "audio_repair.json").write_text(
        json.dumps({"repair_all": True, "segment_ids": [1]}),
        encoding="utf-8",
    )
    return root, segments_path, segments_path.read_bytes()


def _valid_segment() -> dict:
    return {
        "id": 1,
        "start": 0.0,
        "end": 2.0,
        "source_end": 2.0,
        "start_delay_ms": 0,
        "text": "Проверяем старую реплику.",
        "source": "Check the legacy segment.",
        "quality_timing": "legacy",
    }


@pytest.mark.parametrize(
    "segments",
    [
        [{**_valid_segment(), "id": True}],
        [{**_valid_segment(), "id": 1.5}],
        [{**_valid_segment(), "start": float("nan")}],
        [{**_valid_segment(), "start_delay_ms": 1.5, "source_end": 0.0}],
        [_valid_segment(), {**_valid_segment(), "start": 2.0, "end": 4.0}],
        ["not-an-object"],
    ],
)
@pytest.mark.parametrize("runner", ["migration", "normalizer"])
def test_invalid_repair_segments_do_not_replace_source_file(
    monkeypatch,
    tmp_path: Path,
    segments,
    runner: str,
) -> None:
    root, path, before = _project(tmp_path, segments)
    monkeypatch.setattr(migration.policy, "log", lambda _message: None)
    monkeypatch.setattr(normalizer.production, "log", lambda _message: None)

    with pytest.raises(RuntimeError):
        if runner == "migration":
            migration.migrate(root, {"russian_delay_ms": 0})
        else:
            normalizer.normalize(root, {"russian_delay_ms": 0}, duration=5.0)

    assert path.read_bytes() == before
    assert not (root / "segments_ru_final.pre_v45.json").exists()
    assert not (root / "segments_ru_final.pre_clean.json").exists()


def test_repair_preprocessors_delegate_final_validation_to_strict_core() -> None:
    migration_source = Path(migration.__file__).read_text(encoding="utf-8")
    normalizer_source = Path(normalizer.__file__).read_text(encoding="utf-8")
    for source in (migration_source, normalizer_source):
        assert "strict_core._strict_int(" in source
        assert "strict_core._finite(" in source
        assert "strict_core._mark_and_validate_segments(" in source
        assert "allow_nan=False" in source
