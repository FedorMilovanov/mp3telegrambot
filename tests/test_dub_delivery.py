#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

from handlers.dub_delivery import available_outputs


def _project(root: Path) -> dict:
    return {
        "id": "test-project",
        "recipe_id": "short_tnliocegylk",
        "work_root": str(root),
    }


def _touch(root: Path, relative: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"test-output")


def test_default_delivery_sends_primary_video_and_documents(tmp_path: Path) -> None:
    _touch(tmp_path, "output/tNlIoCeGyLk_Russian_Dub_FINAL_UPLOAD.mp4")
    _touch(tmp_path, "output/tNlIoCeGyLk_Russian_Dub_FINAL_RUSSIAN_ONLY.mp4")
    _touch(tmp_path, "output/tNlIoCeGyLk_Russian_Dub_FINAL.srt")
    _touch(tmp_path, "output/tNlIoCeGyLk_Russian_Translation.txt")
    rows = available_outputs(_project(tmp_path), include_all_video=False)
    names = {item["name"] for item in rows}
    assert "mixed" in names
    assert "russian_only" not in names
    assert "russian_subtitles" in names
    assert "translation" in names


def test_all_delivery_includes_russian_only_video(tmp_path: Path) -> None:
    _touch(tmp_path, "output/tNlIoCeGyLk_Russian_Dub_FINAL_UPLOAD.mp4")
    _touch(tmp_path, "output/tNlIoCeGyLk_Russian_Dub_FINAL_RUSSIAN_ONLY.mp4")
    rows = available_outputs(_project(tmp_path), include_all_video=True)
    names = {item["name"] for item in rows}
    assert {"mixed", "russian_only"}.issubset(names)
    assert rows[0]["name"] == "mixed"
