#!/usr/bin/env python3
"""Temporary branch-only refactor runner for the zero-runtime-surgery marathon."""
from __future__ import annotations

import argparse
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    (ROOT / path).write_text(text, encoding="utf-8")


def replace_exact(path: str, old: str, new: str, *, required: bool = True) -> bool:
    text = read(path)
    if old not in text:
        if required:
            raise RuntimeError(f"expected anchor missing in {path}: {old[:120]!r}")
        return False
    write(path, text.replace(old, new, 1))
    print(f"patched {path}: exact replacement")
    return True


def replace_regex(path: str, pattern: str, replacement: str, *, flags: int = 0, required: bool = True) -> bool:
    text = read(path)
    text2, count = re.subn(pattern, replacement, text, count=1, flags=flags)
    if count != 1:
        if required:
            raise RuntimeError(f"expected exactly one regex match in {path}; got {count}: {pattern[:140]!r}")
        return False
    write(path, text2)
    print(f"patched {path}: regex replacement")
    return True


def remove_runtime_feature(text: str, feature_id: str) -> str:
    quoted = r"[\"']" + re.escape(feature_id) + r"[\"']"
    pattern = re.compile(r"\n    RuntimeFeature\(\n        " + quoted + r",.*?\n    \),", re.DOTALL)
    text2, count = pattern.subn("", text, count=1)
    if count != 1:
        raise RuntimeError(f"runtime manifest feature not found exactly once: {feature_id}")
    print(f"removed runtime manifest feature: {feature_id}")
    return text2


def wave1() -> None:
    replace_exact(
        "handlers/commands.py",
        "from pipelines.main_pipeline import process_single_video",
        "from pipelines.video_dispatch import process_single_video",
    )
    replace_regex(
        "services/ffmpeg.py",
        r"async def _is_static_video\(video_path: Path, sample_start: float = 0\.0,\n\s+probe_seconds: float = 6\.0\) -> bool:\n.*?\n\ndef _crop_consensus",
        '''async def _is_static_video(video_path: Path, sample_start: float = 0.0,\n                           probe_seconds: float = 6.0) -> bool:\n    """Delegate visual classification to the explicit static-video policy owner."""\n    from services.shorts_static_runtime import _is_static_video_confident\n\n    return await _is_static_video_confident(\n        video_path, sample_start=sample_start, probe_seconds=probe_seconds\n    )\n\n\ndef _crop_consensus''',
        flags=re.DOTALL,
    )
    static_path = "services/shorts_static_runtime.py"
    static = read(static_path).replace("import sys\n", "").replace("\n_INSTALLED = False\n", "\n")
    static, count = re.subn(
        r"def install_short_static_runtime\(\) -> str:\n.*?(?=\n\n__all__|\Z)",
        '''def install_short_static_runtime() -> str:\n    """Compatibility validator; visual classification is imported by its owner."""\n    if not callable(_is_static_video_confident):\n        raise RuntimeError("static-video classifier is unavailable")\n    return "source-owned static-video classifier; no runtime rebinding"\n''',
        static,
        count=1,
        flags=re.DOTALL,
    )
    if count != 1:
        raise RuntimeError("shorts_static_runtime installer anchor missing")
    write(static_path, static)
    manifest = read("services/runtime_manifest.py")
    manifest = remove_runtime_feature(manifest, "shorts-visual-policy")
    manifest = remove_runtime_feature(manifest, "shorts-factory-routing-bridge")
    write("services/runtime_manifest.py", manifest)
    bridge = ROOT / "services/shorts_factory_overload_editorial_polish.py"
    if bridge.exists():
        bridge.unlink()


def _print_context(path: str, needle: str, radius: int = 18) -> None:
    lines = read(path).splitlines()
    matches = [i for i, line in enumerate(lines) if needle in line]
    print(f"\n### {path} :: {needle!r} matches={len(matches)}")
    for i in matches:
        lo = max(0, i - radius)
        hi = min(len(lines), i + radius + 1)
        print(f"--- lines {lo+1}-{hi} ---")
        for j in range(lo, hi):
            print(f"{j+1:05d}: {lines[j]}")


def diag2() -> None:
    for needle in (
        "_LIVEDUB_TITLE_CACHE",
        "mp3_64_path",
        "create_extras_candidates",
        "process_and_send_shorts",
        "process_and_send_clips",
        "process_and_send_montage",
        "process_and_send_highlights",
        "cleanup_stale_downloads",
        "subprocess.",
    ):
        _print_context("pipelines/main_pipeline.py", needle)
    _print_context("core/utils.py", "def cleanup_stale_downloads")
    _print_context("main.py", "cleanup_stale_cached_audio")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("wave", choices=("wave1", "diag2"))
    args = parser.parse_args()
    globals()[args.wave]()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
