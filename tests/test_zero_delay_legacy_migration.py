from __future__ import annotations

import json
from pathlib import Path

from tools.voxcpm2 import legacy_segment_migration_v45 as migration


def test_legacy_migration_preserves_explicit_zero_delay(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "project"
    (root / "input").mkdir(parents=True)
    (root / "output").mkdir(parents=True)
    (root / "segments_ru_final.json").write_text(
        json.dumps(
            [
                {
                    "id": 1,
                    "start": 0.0,
                    "end": 2.0,
                    "source_end": 2.0,
                    "start_delay_ms": 420,
                    "text": "Нулевая задержка.",
                    "source": "Zero delay.",
                    "quality_timing": "legacy",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (root / "input" / "audio_repair.json").write_text(
        json.dumps({"repair_all": True, "segment_ids": [1]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(migration.policy, "log", lambda _message: None)

    assert migration.migrate(root, {"russian_delay_ms": 0}) is True

    migrated = json.loads(
        (root / "segments_ru_final.json").read_text(encoding="utf-8")
    )
    assert migrated
    assert {item["start_delay_ms"] for item in migrated} == {0}
    assert migrated[-1]["end"] == 2.0
    assert "segments_ru_final.pre_v45.json" in {
        path.name for path in root.iterdir()
    }


def test_legacy_migration_uses_canonical_delay_parser() -> None:
    source = Path(migration.__file__).read_text(encoding="utf-8")
    assert "clean_request_settings.russian_delay_ms(request)" in source
    assert 'request.get("russian_delay_ms") or 420' not in source
