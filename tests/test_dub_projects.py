from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from core.dub_projects import (
    DubProjectError,
    assert_project_owner,
    attach_approved_translation,
    attach_source_file,
    cancel_project,
    create_project,
    extract_project_id,
    load_project,
    project_dir,
    project_marker,
    split_translation_units,
)


@pytest.fixture()
def project_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "dub-projects"
    monkeypatch.setenv("DUB_PROJECTS_DIR", str(root))
    return root


def test_approved_translation_is_locked_and_versioned(project_root: Path) -> None:
    project = create_project(
        owner_user_id=100,
        source={"kind": "url", "url": "https://example.test/video"},
    )
    project_id = project["project_id"]
    text = "[1]\n\nПервый окончательно утверждённый абзац.\n\n[2]\n\nВторой абзац перевода."

    saved = attach_approved_translation(
        project_id,
        text=text,
        approved_by_user_id=100,
        original_filename="translation.md",
    )

    translation = saved["translation"]
    assert saved["schema_version"] == 2
    assert translation["state"] == "approved"
    assert translation["locked"] is True
    assert translation["origin"] == "approved_external"
    assert translation["revision"] == 1
    assert translation["unit_count"] == 2
    assert translation["units_sha256"]
    assert translation["contract_sha256"]
    assert saved["policy"]["rewrite_translation"] is False
    assert saved["policy"]["auto_shorten_translation"] is False
    assert saved["policy"]["synthesis_engine"] == "VoxCPM2"
    assert saved["policy"]["synthesis_device"] == "cpu"

    expected = "[1]\n\nПервый окончательно утверждённый абзац.\n\n[2]\n\nВторой абзац перевода."
    assert Path(translation["display_text_path"]).read_text(encoding="utf-8").strip() == expected
    assert translation["sha256"] == hashlib.sha256(expected.encode("utf-8")).hexdigest()

    units = json.loads(Path(translation["units_path"]).read_text(encoding="utf-8"))
    assert [unit["id"] for unit in units] == ["TU-0001", "TU-0002"]
    assert all(unit["display_text"] == unit["spoken_text"] for unit in units)
    assert (project_dir(project_id) / "events.jsonl").is_file()


def test_explicit_translation_unit_ids_are_preserved() -> None:
    units = split_translation_units(
        "TU-007: Седьмой утверждённый блок.\n\n[12]\nДвенадцатый блок."
    )
    assert [item["id"] for item in units] == ["TU-0007", "TU-0012"]
    assert units[0]["display_text"] == "Седьмой утверждённый блок."
    assert units[1]["display_text"] == "Двенадцатый блок."


def test_duplicate_explicit_translation_unit_is_rejected() -> None:
    with pytest.raises(DubProjectError, match="повторяется"):
        split_translation_units("[3]\nПервый блок.\n\nTU-003\nВторой блок.")


def test_replacing_translation_creates_new_revision(project_root: Path) -> None:
    project_id = create_project(
        owner_user_id=7,
        source={"kind": "url", "url": "https://example.test/one"},
    )["project_id"]
    first = attach_approved_translation(
        project_id,
        text="Первая достаточно длинная утверждённая версия текста.",
        approved_by_user_id=7,
    )
    second = attach_approved_translation(
        project_id,
        text="Вторая достаточно длинная утверждённая версия текста.",
        approved_by_user_id=7,
    )
    assert first["translation"]["revision"] == 1
    assert second["translation"]["revision"] == 2
    assert first["translation"]["sha256"] != second["translation"]["sha256"]
    assert second["production"]["ready"] is False
    assert second["production"]["stage"] == "preflight_pending"
    assert "preflight" not in second


def test_project_marker_and_owner_guard(project_root: Path) -> None:
    manifest = create_project(
        owner_user_id=42,
        source={"kind": "url", "url": "https://example.test/two"},
    )
    project_id = manifest["project_id"]
    marker = project_marker(project_id)
    assert extract_project_id(f"ответ на {marker}") == project_id
    assert extract_project_id("нет проекта") is None
    assert_project_owner(manifest, 42, admin_ids=set())
    assert_project_owner(manifest, 99, admin_ids={99})
    with pytest.raises(DubProjectError):
        assert_project_owner(manifest, 8, admin_ids=set())


def test_source_file_is_hashed_and_cancel_is_durable(project_root: Path, tmp_path: Path) -> None:
    project_id = create_project(
        owner_user_id=1,
        source={
            "kind": "telegram_file",
            "file_id": "file-id",
            "file_unique_id": "unique-id",
            "filename": "source.mp4",
            "mime_type": "video/mp4",
            "file_size": 4,
        },
    )["project_id"]
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video-bytes")
    attached = attach_source_file(project_id, source)
    assert attached["source"]["sha256"] == hashlib.sha256(b"video-bytes").hexdigest()
    assert attached["source"]["local_path"] == str(source.resolve())

    cancelled = cancel_project(project_id, cancelled_by_user_id=1)
    assert cancelled["status"] == "cancelled"
    assert load_project(project_id)["production"]["stage"] == "cancelled"
    with pytest.raises(DubProjectError, match="статусе cancelled"):
        attach_approved_translation(
            project_id,
            text="Новый утверждённый перевод после отмены запрещён.",
            approved_by_user_id=1,
        )


def test_translation_owner_is_checked_inside_store(project_root: Path) -> None:
    project_id = create_project(
        owner_user_id=10,
        source={"kind": "url", "url": "https://example.test/owner"},
    )["project_id"]
    with pytest.raises(DubProjectError, match="владелец"):
        attach_approved_translation(
            project_id,
            text="Достаточно длинный, но не владельцем утверждённый перевод.",
            approved_by_user_id=11,
        )


def test_short_or_empty_translation_is_rejected(project_root: Path) -> None:
    project_id = create_project(
        owner_user_id=1,
        source={"kind": "url", "url": "https://example.test/three"},
    )["project_id"]
    with pytest.raises(DubProjectError):
        attach_approved_translation(project_id, text="слишком мало", approved_by_user_id=1)
