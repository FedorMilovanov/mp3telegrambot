#!/usr/bin/env python3
"""Build a reproducible VoxCPM2 diagnostic ZIP without source video by default."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import zipfile
from pathlib import Path
from typing import Any


DEFAULT_PATTERNS = (
    "**/*.json",
    "**/*.log",
    "**/*.txt",
    "**/*.srt",
    "segment_work/attempts/*.wav",
    "segment_work/segments_clean/*.wav",
    "segment_work/segments_fitted/*.wav",
    "audio/*.wav",
    "references/*.wav",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def collect_files(root: Path, include_video: bool) -> list[Path]:
    seen: set[Path] = set()
    result: list[Path] = []
    for pattern in DEFAULT_PATTERNS:
        for path in root.glob(pattern):
            if not path.is_file():
                continue
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            result.append(path)

    if include_video:
        for pattern in ("source/*.mp4", "output/*.mp4"):
            for path in root.glob(pattern):
                resolved = path.resolve()
                if path.is_file() and resolved not in seen:
                    seen.add(resolved)
                    result.append(path)

    return sorted(result, key=lambda item: item.as_posix().lower())


def build_manifest(root: Path, files: list[Path]) -> dict[str, Any]:
    items = []
    total = 0
    for path in files:
        size = path.stat().st_size
        total += size
        items.append(
            {
                "path": path.relative_to(root).as_posix(),
                "size_bytes": size,
                "sha256": sha256(path),
            }
        )
    return {
        "schema_version": 1,
        "work_root": str(root),
        "file_count": len(items),
        "total_bytes": total,
        "files": items,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--include-video",
        action="store_true",
        help="include source/output MP4 files; disabled by default",
    )
    args = parser.parse_args()

    root = args.work_root.expanduser().resolve()
    output = args.output.expanduser().resolve()
    if not root.is_dir():
        raise SystemExit(f"work root not found: {root}")

    files = collect_files(root, include_video=args.include_video)
    if not files:
        raise SystemExit(f"no diagnostic files found under: {root}")

    manifest = build_manifest(root, files)
    output.parent.mkdir(parents=True, exist_ok=True)

    temp = output.with_suffix(output.suffix + ".tmp")
    if temp.exists():
        temp.unlink()

    with zipfile.ZipFile(
        temp,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=6,
    ) as archive:
        for path in files:
            archive.write(path, arcname=path.relative_to(root).as_posix())
        archive.writestr(
            "diagnostic_manifest.json",
            json.dumps(manifest, ensure_ascii=False, indent=2),
        )

    os.replace(temp, output)
    print(json.dumps({"output": str(output), **manifest}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
