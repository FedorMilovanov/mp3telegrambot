from __future__ import annotations

import json
from pathlib import Path

from services import livedub_audio_cache_recovery as recovery


def _write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _install(monkeypatch, tmp_path: Path, *, base_save=None, base_load=None):
    import services.livedub_audio_companion as companion

    path = tmp_path / "livedub-audio-file-ids.json"
    monkeypatch.setattr(companion, "_cache_path", lambda: path)
    monkeypatch.setattr(companion, "_load_cache", base_load or (lambda: {}))

    if base_save is None:
        def base_save(data):
            _write(path, recovery._expected_generation(data))
    monkeypatch.setattr(companion, "_save_cache", base_save)
    recovery._install_cache_recovery()
    return companion, path, path.with_suffix(path.suffix + ".bak")


def test_corrupt_primary_recovers_valid_backup_and_repairs_disk(tmp_path: Path, monkeypatch):
    companion, path, backup = _install(monkeypatch, tmp_path)
    expected = {"video-old": {"saved_at": 1, "variants": {}}}
    path.write_text("{broken", encoding="utf-8")
    _write(backup, expected)

    assert companion._load_cache() == expected
    assert json.loads(path.read_text(encoding="utf-8")) == expected


def test_successful_save_keeps_previous_valid_generation_in_backup(tmp_path: Path, monkeypatch):
    companion, path, backup = _install(monkeypatch, tmp_path)
    old = {"video-old": {"saved_at": 1}}
    new = {"video-new": {"saved_at": 2}}
    _write(path, old)

    companion._save_cache(new)

    assert json.loads(path.read_text(encoding="utf-8")) == new
    assert json.loads(backup.read_text(encoding="utf-8")) == old


def test_invalid_new_generation_rolls_back_previous_cache(tmp_path: Path, monkeypatch):
    path = tmp_path / "livedub-audio-file-ids.json"

    def broken_save(_data):
        path.write_text("not-json", encoding="utf-8")

    companion, path, backup = _install(
        monkeypatch,
        tmp_path,
        base_save=broken_save,
    )
    old = {"video-old": {"saved_at": 1}}
    _write(path, old)

    companion._save_cache({"video-new": {"saved_at": 2}})

    assert json.loads(path.read_text(encoding="utf-8")) == old
    assert json.loads(backup.read_text(encoding="utf-8")) == old


def test_valid_but_wrong_generation_rolls_back_previous_cache(tmp_path: Path, monkeypatch):
    path = tmp_path / "livedub-audio-file-ids.json"

    def stale_save(_data):
        _write(path, {"unrelated": {"saved_at": 99}})

    companion, path, backup = _install(
        monkeypatch,
        tmp_path,
        base_save=stale_save,
    )
    old = {"video-old": {"saved_at": 1}}
    expected_new = {"video-new": {"saved_at": 2}}
    _write(path, old)

    companion._save_cache(expected_new)

    assert json.loads(path.read_text(encoding="utf-8")) == old
    assert json.loads(backup.read_text(encoding="utf-8")) == old


def test_noop_save_cannot_pass_because_old_primary_is_still_valid(tmp_path: Path, monkeypatch):
    companion, path, backup = _install(
        monkeypatch,
        tmp_path,
        base_save=lambda _data: None,
    )
    old = {"video-old": {"saved_at": 1}}
    _write(path, old)

    companion._save_cache({"video-new": {"saved_at": 2}})

    assert json.loads(path.read_text(encoding="utf-8")) == old
    assert json.loads(backup.read_text(encoding="utf-8")) == old


def test_wrong_generation_without_backup_is_discarded(tmp_path: Path, monkeypatch):
    path = tmp_path / "livedub-audio-file-ids.json"

    def wrong_save(_data):
        _write(path, {"wrong": {"saved_at": 9}})

    companion, path, backup = _install(
        monkeypatch,
        tmp_path,
        base_save=wrong_save,
    )

    companion._save_cache({"wanted": {"saved_at": 10}})

    assert not path.exists()
    assert not backup.exists()


def test_corrupt_primary_is_not_promoted_over_existing_backup(tmp_path: Path, monkeypatch):
    companion, path, backup = _install(monkeypatch, tmp_path)
    old = {"video-safe": {"saved_at": 1}}
    path.write_text("corrupt", encoding="utf-8")
    _write(backup, old)

    companion._save_cache({"video-new": {"saved_at": 2}})

    assert json.loads(path.read_text(encoding="utf-8")) == {"video-new": {"saved_at": 2}}
    assert json.loads(backup.read_text(encoding="utf-8")) == old


def test_recovered_cache_is_preserved_when_new_variant_is_added(tmp_path: Path, monkeypatch):
    companion, path, backup = _install(monkeypatch, tmp_path)
    old = {
        "old-video": {
            "schema_version": 2,
            "saved_at": 1,
            "variants": {
                "clean": {"audio_file_id": "old-clean"},
                "mixed": {"audio_file_id": "old-mixed"},
            },
        }
    }
    path.write_text("{damaged", encoding="utf-8")
    _write(backup, old)

    companion._cache_put_variant(
        "new-video",
        "clean",
        "new-clean",
        title="New",
    )

    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["old-video"]["variants"]["mixed"]["audio_file_id"] == "old-mixed"
    assert saved["new-video"]["variants"]["clean"]["audio_file_id"] == "new-clean"


def test_expected_generation_matches_newest_500_contract():
    data = {
        f"video-{idx}": {"saved_at": idx}
        for idx in range(510)
    }

    expected = recovery._expected_generation(data)

    assert len(expected) == 500
    assert "video-509" in expected
    assert "video-10" in expected
    assert "video-9" not in expected


def test_bounded_reader_rejects_oversized_or_non_object_json(tmp_path: Path):
    oversized = tmp_path / "oversized.json"
    oversized.write_text(json.dumps({"x": "a" * 200}), encoding="utf-8")
    assert recovery._read_mapping(oversized, max_bytes=32) is None

    array = tmp_path / "array.json"
    array.write_text("[]", encoding="utf-8")
    assert recovery._read_mapping(array) is None
