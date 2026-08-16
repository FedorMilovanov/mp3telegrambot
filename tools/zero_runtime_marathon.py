#!/usr/bin/env python3
"""Temporary branch-only refactor runner for the zero-runtime-surgery marathon.

This file is deleted before merge. It exists only because the connected GitHub
API can write files but cannot apply arbitrary hunks to very large source files.
Every edit is fail-closed: expected anchors must exist or the run aborts.
"""
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
    text2 = text.replace(old, new, 1)
    write(path, text2)
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
    pattern = re.compile(
        r"\n    RuntimeFeature\(\n        " + re.escape(repr(feature_id)) + r",.*?\n    \),",
        re.DOTALL,
    )
    text2, count = pattern.subn("", text, count=1)
    if count != 1:
        raise RuntimeError(f"runtime manifest feature not found exactly once: {feature_id}")
    print(f"removed runtime manifest feature: {feature_id}")
    return text2


def wave1() -> None:
    # The dispatcher is already the source owner; commands must import it directly.
    replace_exact(
        "handlers/commands.py",
        "from pipelines.main_pipeline import process_single_video",
        "from pipelines.video_dispatch import process_single_video",
    )

    # Keep the conservative detector implementation, but make services.ffmpeg the
    # direct owner boundary instead of replacing its function through sys.modules.
    replace_regex(
        "services/ffmpeg.py",
        r"async def _is_static_video\(video_path: Path, sample_start: float = 0\.0,\n\s+probe_seconds: float = 6\.0\) -> bool:\n.*?\n\ndef _crop_consensus",
        '''async def _is_static_video(video_path: Path, sample_start: float = 0.0,\n                           probe_seconds: float = 6.0) -> bool:\n    """Delegate visual classification to the explicit static-video policy owner."""\n    from services.shorts_static_runtime import _is_static_video_confident\n\n    return await _is_static_video_confident(\n        video_path,\n        sample_start=sample_start,\n        probe_seconds=probe_seconds,\n    )\n\n\ndef _crop_consensus''',
        flags=re.DOTALL,
    )

    # The old installer becomes a compatibility validator only. No sys.modules,
    # imported-module assignment or setattr remains.
    static_path = "services/shorts_static_runtime.py"
    static = read(static_path)
    static = static.replace("import sys\n", "")
    static = static.replace("\n_INSTALLED = False\n", "\n")
    pattern = re.compile(
        r"def install_short_static_runtime\(\) -> str:\n.*?(?=\n\n__all__|\Z)",
        re.DOTALL,
    )
    replacement = '''def install_short_static_runtime() -> str:\n    """Compatibility validator; visual classification is imported by its owner."""\n    if not callable(_is_static_video_confident):\n        raise RuntimeError("static-video classifier is unavailable")\n    return "source-owned static-video classifier; no runtime rebinding"\n'''
    static2, count = pattern.subn(replacement, static, count=1)
    if count != 1:
        raise RuntimeError("shorts_static_runtime installer anchor missing")
    write(static_path, static2)
    print("patched services/shorts_static_runtime.py: removed runtime rebinding")

    # Source-owned dispatcher and static classifier make these manifest adapters obsolete.
    manifest_path = "services/runtime_manifest.py"
    manifest = read(manifest_path)
    manifest = remove_runtime_feature(manifest, "shorts-visual-policy")
    manifest = remove_runtime_feature(manifest, "shorts-factory-routing-bridge")
    write(manifest_path, manifest)

    # The routing bridge has no remaining production responsibility.
    bridge = ROOT / "services/shorts_factory_overload_editorial_polish.py"
    if bridge.exists():
        bridge.unlink()
        print("deleted services/shorts_factory_overload_editorial_polish.py")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("wave", choices=("wave1",))
    args = parser.parse_args()
    if args.wave == "wave1":
        wave1()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
