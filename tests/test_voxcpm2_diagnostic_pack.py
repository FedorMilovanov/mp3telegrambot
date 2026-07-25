from __future__ import annotations

import importlib.util
import json
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools" / "voxcpm2" / "diagnostic_pack.py"


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "voxcpm2_diagnostic_pack",
        MODULE_PATH,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


diagnostic = _load_module()


def test_collect_files_excludes_video_by_default(tmp_path: Path) -> None:
    (tmp_path / "audio").mkdir()
    (tmp_path / "source").mkdir()
    (tmp_path / "audio" / "timeline.wav").write_bytes(b"wav")
    (tmp_path / "source" / "source.mp4").write_bytes(b"video")
    (tmp_path / "run.json").write_text("{}", encoding="utf-8")

    files = diagnostic.collect_files(tmp_path, include_video=False)
    relative = {path.relative_to(tmp_path).as_posix() for path in files}

    assert "audio/timeline.wav" in relative
    assert "run.json" in relative
    assert "source/source.mp4" not in relative


def test_collect_files_can_include_video(tmp_path: Path) -> None:
    (tmp_path / "source").mkdir()
    (tmp_path / "source" / "source.mp4").write_bytes(b"video")
    (tmp_path / "run.json").write_text("{}", encoding="utf-8")

    files = diagnostic.collect_files(tmp_path, include_video=True)
    relative = {path.relative_to(tmp_path).as_posix() for path in files}

    assert "source/source.mp4" in relative


def test_manifest_contains_hashes(tmp_path: Path) -> None:
    file_path = tmp_path / "run.json"
    file_path.write_text('{"ok": true}', encoding="utf-8")

    manifest = diagnostic.build_manifest(tmp_path, [file_path])

    assert manifest["file_count"] == 1
    assert manifest["files"][0]["path"] == "run.json"
    assert len(manifest["files"][0]["sha256"]) == 64


def test_zip_can_store_manifest(tmp_path: Path) -> None:
    data = tmp_path / "run.json"
    data.write_text('{"ok": true}', encoding="utf-8")
    output = tmp_path / "diag.zip"
    manifest = diagnostic.build_manifest(tmp_path, [data])

    with zipfile.ZipFile(output, "w") as archive:
        archive.write(data, arcname="run.json")
        archive.writestr(
            "diagnostic_manifest.json",
            json.dumps(manifest, ensure_ascii=False, indent=2),
        )

    with zipfile.ZipFile(output) as archive:
        assert "run.json" in archive.namelist()
        assert "diagnostic_manifest.json" in archive.namelist()
