#!/usr/bin/env python3
"""One-time fail-closed patch for English subtitle media ownership."""
from __future__ import annotations

import ast
import re
from collections.abc import Callable
from pathlib import Path


PATH = Path("services/eng_subtitles.py")


def _function_bounds(source: str, name: str) -> tuple[int, int]:
    tree = ast.parse(source)
    node = next(
        item
        for item in tree.body
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
        and item.name == name
    )
    lines = source.splitlines(keepends=True)
    start = sum(len(line) for line in lines[: node.lineno - 1])
    end = sum(len(line) for line in lines[: node.end_lineno])
    return start, end


def _transform_function(
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
        raise SystemExit(f"{label}: expected one exact match, found {count}")
    return text.replace(old, new, 1)


def _replace_regex_once(
    text: str,
    pattern: str,
    replacement: str,
    *,
    label: str,
) -> str:
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.DOTALL)
    if count != 1:
        raise SystemExit(f"{label}: expected one regex match, found {count}")
    return updated


def _patch_create_subtitles(function: str) -> str:
    function = _replace_regex_once(
        function,
        r"    def _run_cmd\(t\):\n.*?    proc = await loop\.run_in_executor\(None, lambda: _run_cmd\(300\)\)\n",
        "    proc = await run_cancellable_process(\n"
        "        cmd, timeout=300, text=True\n"
        "    )\n",
        label="subtitle audio download",
    )
    function = _replace_once(
        function,
        "    audio_duration = _get_audio_duration(actual_audio)\n",
        "    audio_duration = await _get_audio_duration(actual_audio)\n",
        label="async ffprobe call",
    )
    function = _replace_once(
        function,
        "    segments = await loop.run_in_executor(None, _run_whisper)\n",
        "    segments = await await_owned_coroutine(\n"
        "        asyncio.to_thread(_run_whisper)\n"
        "    )\n",
        label="owned Whisper worker",
    )
    return function


def _patch_download_video(function: str) -> str:
    function = _replace_regex_once(
        function,
        r"    def _run_cmd\(t\):\n.*?    proc = await loop\.run_in_executor\(None, lambda: _run_cmd\(900\)\)\n",
        "    proc = await run_cancellable_process(\n"
        "        cmd, timeout=900, text=True\n"
        "    )\n"
        "    if proc.returncode != 0:\n"
        "        raise RuntimeError(\n"
        "            f\"Не удалось скачать оригинальное видео (yt-dlp rc={proc.returncode}). \"\n"
        "            f\"stderr: {proc.stderr[-500:] if proc.stderr else ''}\"\n"
        "        )\n",
        label="original video download",
    )
    return function


def _patch_burn(function: str) -> str:
    function = _replace_regex_once(
        function,
        r"    def _run\(t\):\n.*?    try:\n        proc = await loop\.run_in_executor\(None, lambda: _run\(600\)\)\n",
        "    output_path.unlink(missing_ok=True)\n"
        "    try:\n"
        "        proc = await run_cancellable_process(\n"
        "            cmd, timeout=600, text=True\n"
        "        )\n",
        label="hardsub process owner",
    )
    function = _replace_once(
        function,
        "    except Exception as e:\n"
        "        logger.warning(\"[EngSubtitles] hardsub exception: %s\", e)\n"
        "        return None\n",
        "    except asyncio.CancelledError:\n"
        "        output_path.unlink(missing_ok=True)\n"
        "        raise\n"
        "    except Exception as e:\n"
        "        output_path.unlink(missing_ok=True)\n"
        "        logger.warning(\"[EngSubtitles] hardsub exception: %s\", e)\n"
        "        return None\n",
        label="hardsub exception cleanup",
    )
    function = _replace_once(
        function,
        "    logger.warning(\"[EngSubtitles] hardsub rc=%s: %s\", proc.returncode,\n"
        "                   (proc.stderr or \"\")[-300:])\n"
        "    return None\n",
        "    logger.warning(\"[EngSubtitles] hardsub rc=%s: %s\", proc.returncode,\n"
        "                   (proc.stderr or \"\")[-300:])\n"
        "    output_path.unlink(missing_ok=True)\n"
        "    return None\n",
        label="hardsub failure cleanup",
    )
    return function


def _patch_merge(function: str) -> str:
    function = _replace_regex_once(
        function,
        r"    def _run_cmd\(t\):\n.*?    proc = await loop\.run_in_executor\(None, lambda: _run_cmd\(300\)\)\n",
        "    output_path.unlink(missing_ok=True)\n"
        "    try:\n"
        "        proc = await run_cancellable_process(\n"
        "            cmd, timeout=300, text=True\n"
        "        )\n"
        "    except BaseException:\n"
        "        output_path.unlink(missing_ok=True)\n"
        "        raise\n",
        label="primary subtitle mux",
    )
    function = _replace_regex_once(
        function,
        r"    def _run_cmd_fallback\(t\):\n.*?    proc2 = await loop\.run_in_executor\(None, lambda: _run_cmd_fallback\(300\)\)\n",
        "    output_path.unlink(missing_ok=True)\n"
        "    try:\n"
        "        proc2 = await run_cancellable_process(\n"
        "            cmd_fallback, timeout=300, text=True\n"
        "        )\n"
        "    except BaseException:\n"
        "        output_path.unlink(missing_ok=True)\n"
        "        raise\n",
        label="fallback subtitle mux",
    )
    function = _replace_once(
        function,
        "    logger.error(f\"[EngSubtitles] Полный отказ склейки. stderr: {proc2.stderr[-300:] if proc2.stderr else ''}\")\n"
        "    return video_path\n",
        "    logger.error(f\"[EngSubtitles] Полный отказ склейки. stderr: {proc2.stderr[-300:] if proc2.stderr else ''}\")\n"
        "    output_path.unlink(missing_ok=True)\n"
        "    return video_path\n",
        label="mux failure cleanup",
    )
    return function


def main() -> None:
    source = PATH.read_text(encoding="utf-8")
    source = _replace_once(
        source,
        "import subprocess\n",
        "",
        label="remove direct subprocess import",
    )
    source = _replace_once(
        source,
        "from services.ffmpeg import YTDLP_BASE_ARGS\n",
        "from services.ffmpeg import YTDLP_BASE_ARGS\n"
        "from services.async_process import run_cancellable_process\n"
        "from services.async_worker import await_owned_coroutine\n",
        label="owner imports",
    )

    start, end = _function_bounds(source, "_get_audio_duration")
    source = source[:start] + (
        "async def _get_audio_duration(path: Path) -> float:\n"
        "    ffprobe = shutil.which(\"ffprobe\")\n"
        "    if not ffprobe:\n"
        "        return 0.0\n"
        "    try:\n"
        "        result = await run_cancellable_process(\n"
        "            [\n"
        "                ffprobe, \"-v\", \"error\",\n"
        "                \"-show_entries\", \"format=duration\",\n"
        "                \"-of\", \"default=noprint_wrappers=1:nokey=1\",\n"
        "                str(path),\n"
        "            ],\n"
        "            timeout=30,\n"
        "            text=True,\n"
        "        )\n"
        "        if result.returncode != 0:\n"
        "            return 0.0\n"
        "        return float((result.stdout or \"\").strip())\n"
        "    except Exception as exc:\n"
        "        logger.debug(\"[EngSubtitles] ffprobe duration failed: %s\", exc)\n"
        "        return 0.0\n"
    ) + source[end:]

    source = _transform_function(
        source,
        "create_gemini_subtitles",
        _patch_create_subtitles,
    )
    source = _transform_function(
        source,
        "download_original_video",
        _patch_download_video,
    )
    source = _transform_function(source, "_burn_subtitles", _patch_burn)
    source = _transform_function(source, "merge_subtitles", _patch_merge)

    ast.parse(source)
    if "run_in_executor" in source:
        raise SystemExit("run_in_executor remains in eng_subtitles.py")
    if "shell=" in source:
        raise SystemExit("shell execution remains in eng_subtitles.py")
    if source.count("await run_cancellable_process(") != 6:
        raise SystemExit("expected six process-owner calls")
    if source.count("asyncio.to_thread(_run_whisper)") != 1:
        raise SystemExit("owned Whisper postcondition failed")
    if source.count("output_path.unlink(missing_ok=True)") < 7:
        raise SystemExit("transactional output cleanup postcondition failed")

    PATH.write_text(source, encoding="utf-8")
    print("patched services/eng_subtitles.py ownership and cleanup")


if __name__ == "__main__":
    main()
