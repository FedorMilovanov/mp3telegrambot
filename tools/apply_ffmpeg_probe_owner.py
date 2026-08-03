#!/usr/bin/env python3
"""One-time fail-closed patch for async FFmpeg and yt-dlp probes."""
from __future__ import annotations

import ast
from collections.abc import Callable
from pathlib import Path


PATH = Path("services/ffmpeg.py")
CI_PATH = Path(".github/workflows/ci.yml")


def _function_bounds(source: str, name: str) -> tuple[int, int]:
    tree = ast.parse(source)
    node = next(
        item
        for item in tree.body
        if isinstance(item, ast.AsyncFunctionDef) and item.name == name
    )
    lines = source.splitlines(keepends=True)
    start = sum(len(line) for line in lines[: node.lineno - 1])
    end = sum(len(line) for line in lines[: node.end_lineno])
    return start, end


def _transform(
    source: str,
    name: str,
    transform: Callable[[str], str],
) -> str:
    start, end = _function_bounds(source, name)
    original = source[start:end]
    updated = transform(original)
    if updated == original:
        raise SystemExit(f"{name}: transform made no change")
    return source[:start] + updated + source[end:]


def _replace_once(text: str, old: str, new: str, *, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


def _patch_silence(function: str) -> str:
    return _replace_once(
        function,
        "        proc = await asyncio.get_running_loop().run_in_executor(\n"
        "            None,\n"
        "            lambda: subprocess.run(cmd, capture_output=True, text=True, timeout=30),\n"
        "        )",
        "        proc = await run_cancellable_process(\n"
        "            cmd, timeout=30, text=True\n"
        "        )",
        label="silence probe",
    )


def _patch_static(function: str) -> str:
    return _replace_once(
        function,
        "        proc = await asyncio.get_running_loop().run_in_executor(\n"
        "            None,\n"
        "            lambda: subprocess.run(cmd, capture_output=True, text=True, timeout=30),\n"
        "        )",
        "        proc = await run_cancellable_process(\n"
        "            cmd, timeout=30, text=True\n"
        "        )",
        label="freeze probe",
    )


def _patch_crop(function: str) -> str:
    function = _replace_once(
        function,
        "        loop = asyncio.get_running_loop()\n\n",
        "",
        label="crop loop alias",
    )
    function = _replace_once(
        function,
        "        try:\n"
        "            from subprocess import run as _sp_run\n"
        "            probe = _sp_run(\n"
        "                [ffmpeg, \"-i\", str(video_path)],\n"
        "                capture_output=True, text=True, timeout=10,\n"
        "            )\n",
        "        try:\n"
        "            probe = await run_cancellable_process(\n"
        "                [ffmpeg, \"-i\", str(video_path)],\n"
        "                timeout=10, text=True,\n"
        "            )\n",
        label="duration header probe",
    )
    function = _replace_once(
        function,
        "            proc = await loop.run_in_executor(\n"
        "                None,\n"
        "                lambda c=cmd: subprocess.run(c, capture_output=True, text=True, timeout=30),\n"
        "            )",
        "            proc = await run_cancellable_process(\n"
        "                cmd, timeout=30, text=True\n"
        "            )",
        label="crop sample probe",
    )
    function = _replace_once(
        function,
        "        probe = await loop.run_in_executor(\n"
        "            None,\n"
        "            lambda: subprocess.run(probe_cmd, capture_output=True, text=True, timeout=5),\n"
        "        )",
        "        probe = await run_cancellable_process(\n"
        "            probe_cmd, timeout=5, text=True\n"
        "        )",
        label="crop dimension probe",
    )
    return function


def _patch_language(function: str) -> str:
    function = _replace_once(
        function,
        "        loop = asyncio.get_running_loop()\n",
        "",
        label="language loop alias",
    )
    return _replace_once(
        function,
        "        proc = await loop.run_in_executor(\n"
        "            None,\n"
        "            lambda: subprocess.run(cmd, capture_output=True, text=True, timeout=30)\n"
        "        )",
        "        proc = await run_cancellable_process(\n"
        "            cmd, timeout=30, text=True\n"
        "        )",
        label="language metadata probe",
    )


def main() -> None:
    source = PATH.read_text(encoding="utf-8")
    source = _replace_once(
        source,
        "from urllib.parse import urlparse\n",
        "from urllib.parse import urlparse\n\n"
        "from services.async_process import run_cancellable_process\n",
        label="process owner import",
    )
    source = _transform(source, "_find_silence_end", _patch_silence)
    source = _transform(source, "_is_static_video", _patch_static)
    source = _transform(source, "_detect_black_bars", _patch_crop)
    source = _transform(source, "probe_video_language", _patch_language)

    ast.parse(source)
    selected = "\n".join(
        source[_function_bounds(source, name)[0] : _function_bounds(source, name)[1]]
        for name in (
            "_find_silence_end",
            "_is_static_video",
            "_detect_black_bars",
            "probe_video_language",
        )
    )
    if "run_in_executor" in selected:
        raise SystemExit("executor subprocess remains in async probe surface")
    if "from subprocess import run" in selected:
        raise SystemExit("blocking subprocess import remains in async probe surface")
    if selected.count("await run_cancellable_process(") != 6:
        raise SystemExit("expected six process-owner call sites")

    ci = CI_PATH.read_text(encoding="utf-8")
    anchor = "          tests/test_async_process_tree.py\n"
    replacement = anchor + "          tests/test_ffmpeg_probe_ownership.py\n"
    if ci.count(anchor) != 2:
        raise SystemExit("Windows CI async-process anchor count changed")
    ci = ci.replace(anchor, replacement)

    PATH.write_text(source, encoding="utf-8")
    CI_PATH.write_text(ci, encoding="utf-8")
    print("patched six async FFmpeg/yt-dlp probes and Windows CI selection")


if __name__ == "__main__":
    main()
