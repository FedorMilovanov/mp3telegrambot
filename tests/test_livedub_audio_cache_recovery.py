from __future__ import annotations

import json
from pathlib import Path

import pytest

from services import livedub_audio_cache_recovery as recovery


def _write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_corrupt_primary_recovers_valid_backup_and_repairs_disk(tmp_path: Path) -> None:
    path = tmp_path / "cache.json"
    backup = path.with_suffix(path.suffix + ".bak")
    expected = {"video-old": {"saved_at": 1}}
    path.write_text("{broken", encoding="utf-8")
    _write(backup, expected)
    assert recovery.load_recoverable_cache(path) == expected
    assert json.loads(path.read_text(encoding="utf-8")) == expected


def test_successful_save_keeps_previous_valid_generation_in_backup(tmp_path: Path) -> None:
    path = tmp_path / "cache.json"
    backup = path.with_suffix(path.suffix + ".bak")
    old = {"video-old": {"saved_at": 1}}
    new = {"video-new": {"saved_at": 2}}
    _write(path, old)
    recovery.save_recoverable_cache(path, new)
    assert json.loads(path.read_text(encoding="utf-8")) == new
    assert json.loads(backup.read_text(encoding="utf-8")) == old


@pytest.mark.parametrize("mode", ["corrupt", "wrong", "noop"])
def test_invalid_new_generation_rolls_back_previous_cache(tmp_path: Path, monkeypatch, mode: str) -> None:
    path = tmp_path / "cache.json"
    backup = path.with_suffix(path.suffix + ".bak")
    old = {"video-old": {"saved_at": 1}}
    wanted = {"video-new": {"saved_at": 2}}
    _write(path, old)

    def broken(target: Path, data):
        if mode == "corrupt":
            target.write_text("not-json", encoding="utf-8")
        elif mode == "wrong":
            _write(target, {"wrong": {"saved_at": 99}})
        else:
            return None

    monkeypatch.setattr(recovery, "_atomic_write", broken)
    with pytest.raises(RuntimeError, match="differs from requested generation"):
        recovery.save_recoverable_cache(path, wanted)
    assert json.loads(path.read_text(encoding="utf-8")) == old
    assert json.loads(backup.read_text(encoding="utf-8")) == old


def test_wrong_generation_without_backup_is_discarded(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "cache.json"
    monkeypatch.setattr(recovery, "_atomic_write", lambda target, data: _write(target, {"wrong": {"saved_at": 9}}))
    with pytest.raises(RuntimeError):
        recovery.save_recoverable_cache(path, {"wanted": {"saved_at": 10}})
    assert not path.exists()


def test_recovered_cache_is_preserved_when_companion_adds_variant(tmp_path: Path, monkeypatch) -> None:
    import services.livedub_audio_companion as companion

    path = tmp_path / "cache.json"
    backup = path.with_suffix(path.suffix + ".bak")
    old = {
        "old-video": {
            "schema_version": 2, "saved_at": 1,
            "variants": {"clean": {"audio_file_id": "old-clean"}, "mixed": {"audio_file_id": "old-mixed"}},
        }
    }
    path.write_text("{damaged", encoding="utf-8")
    _write(backup, old)
    monkeypatch.setattr(companion, "_cache_path", lambda: path)
    companion._cache_put_variant("new-video", "clean", "new-clean", title="New")
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["old-video"]["variants"]["mixed"]["audio_file_id"] == "old-mixed"
    assert saved["new-video"]["variants"]["clean"]["audio_file_id"] == "new-clean"


def test_expected_generation_keeps_newest_500() -> None:
    data = {f"video-{idx}": {"saved_at": idx} for idx in range(510)}
    expected = recovery._expected_generation(data)
    assert len(expected) == 500
    assert "video-509" in expected and "video-10" in expected
    assert "video-9" not in expected


def test_bounded_reader_rejects_oversized_or_non_object_json(tmp_path: Path) -> None:
    oversized = tmp_path / "oversized.json"
    oversized.write_text(json.dumps({"x": "a" * 200}), encoding="utf-8")
    assert recovery._read_mapping(oversized, max_bytes=32) is None
    array = tmp_path / "array.json"
    array.write_text("[]", encoding="utf-8")
    assert recovery._read_mapping(array) is None
