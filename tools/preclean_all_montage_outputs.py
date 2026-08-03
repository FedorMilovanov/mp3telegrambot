#!/usr/bin/env python3
"""Precompute and clean every deterministic Montage artifact before rendering."""
from __future__ import annotations

import ast
from pathlib import Path


PATH = Path("services/render_clips_montage.py")


def _replace_once(source: str, old: str, new: str, *, label: str) -> str:
    count = source.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    return source.replace(old, new, 1)


def main() -> None:
    source = PATH.read_text(encoding="utf-8")
    source = _replace_once(
        source,
        "    temp_parts: list[Path] = []\n"
        "    concat_list_path: Path | None = None\n"
        "    _unlink_render_paths(output_path)\n"
        "    try:\n",
        "    temp_parts = [\n"
        "        output_path.parent / f\"{output_path.stem}_part{i}.mp4\"\n"
        "        for i in range(len(fragments))\n"
        "    ]\n"
        "    concat_list_path: Path | None = (\n"
        "        output_path.parent / f\"{output_path.stem}_concat.txt\"\n"
        "    )\n"
        "    stale_parts = list(\n"
        "        output_path.parent.glob(f\"{output_path.stem}_part*.mp4\")\n"
        "    )\n"
        "    _unlink_render_paths(\n"
        "        *stale_parts, *temp_parts, concat_list_path, output_path\n"
        "    )\n"
        "    try:\n",
        label="montage deterministic preclean",
    )
    source = _replace_once(
        source,
        "        for i, frag in enumerate(fragments):\n"
        "            part_path = output_path.parent / f\"{output_path.stem}_part{i}.mp4\"\n"
        "            _unlink_render_paths(part_path)\n"
        "            temp_parts.append(part_path)\n",
        "        for i, frag in enumerate(fragments):\n"
        "            part_path = temp_parts[i]\n",
        label="montage precomputed part use",
    )
    source = _replace_once(
        source,
        "        concat_list_path = output_path.parent / f\"{output_path.stem}_concat.txt\"\n"
        "        _unlink_render_paths(concat_list_path)\n"
        "        with open(concat_list_path, \"w\", encoding=\"utf-8\") as f:\n",
        "        assert concat_list_path is not None\n"
        "        with open(concat_list_path, \"w\", encoding=\"utf-8\") as f:\n",
        label="montage precomputed concat use",
    )

    ast.parse(source)
    start = source.index("async def render_montage_short(")
    function = source[start:]
    if "stale_parts = list(" not in function:
        raise SystemExit("stale Montage part glob missing")
    if "part_path = temp_parts[i]" not in function:
        raise SystemExit("precomputed part path not used")
    if "temp_parts.append(" in function:
        raise SystemExit("lazy part tracking remains")
    if function.count("output_path.parent / f\"{output_path.stem}_concat.txt\"") != 1:
        raise SystemExit("concat path must be computed exactly once")

    PATH.write_text(source, encoding="utf-8")
    print("precleaned all deterministic Montage artifacts before first FFmpeg")


if __name__ == "__main__":
    main()
