#!/usr/bin/env python3
"""One-time branch patch for process ownership in the main media pipeline."""
from __future__ import annotations

import ast
import re
from pathlib import Path


PATH = Path("pipelines/main_pipeline.py")


def _replace_exact(source: str, old: str, new: str, *, expected: int, label: str) -> str:
    found = source.count(old)
    if found != expected:
        raise SystemExit(f"{label}: expected {expected} exact matches, found {found}")
    return source.replace(old, new)


def _replace_regex(source: str, pattern: str, replacement, *, expected: int, label: str) -> str:
    updated, count = re.subn(pattern, replacement, source, flags=re.MULTILINE)
    if count != expected:
        raise SystemExit(f"{label}: expected {expected} regex matches, found {count}")
    return updated


def main() -> None:
    source = PATH.read_text(encoding="utf-8")

    source = _replace_exact(
        source,
        "from services.async_worker import await_owned_with_soft_timeout\n",
        "from services.async_process import run_cancellable_process\n"
        "from services.async_worker import (\n"
        "    await_owned_coroutine,\n"
        "    await_owned_with_soft_timeout,\n"
        ")\n",
        expected=1,
        label="imports",
    )

    source = _replace_regex(
        source,
        r"(?P<i>^[ \t]+)info_proc = await asyncio\.get_running_loop\(\)\.run_in_executor\(\n"
        r"(?P=i)    None, lambda: subprocess\.run\(info_cmd, capture_output=True, text=True, encoding=\"utf-8\", errors=\"replace\", timeout=_info_timeout\)\n"
        r"(?P=i)\)",
        lambda m: (
            f"{m.group('i')}info_proc = await run_cancellable_process(\n"
            f"{m.group('i')}    info_cmd, timeout=_info_timeout, text=True\n"
            f"{m.group('i')})"
        ),
        expected=1,
        label="metadata subprocess fallback",
    )

    source = _replace_regex(
        source,
        r"(?P<i>^[ \t]+)_thumb_proc = await asyncio\.get_running_loop\(\)\.run_in_executor\(\n"
        r"(?P=i)    None, lambda: subprocess\.run\(thumb_cmd, capture_output=True, text=True, timeout=30\)\n"
        r"(?P=i)\)",
        lambda m: (
            f"{m.group('i')}_thumb_proc = await run_cancellable_process(\n"
            f"{m.group('i')}    thumb_cmd, timeout=30, text=True\n"
            f"{m.group('i')})"
        ),
        expected=2,
        label="thumbnail subprocesses",
    )

    source = _replace_regex(
        source,
        r"(?P<i>^[ \t]+)await asyncio\.get_running_loop\(\)\.run_in_executor\(\n"
        r"(?P=i)    None, lambda: subprocess\.run\(dl_cmd, capture_output=True, timeout=1800\)\)",
        lambda m: (
            f"{m.group('i')}_cached_audio_proc = await run_cancellable_process(\n"
            f"{m.group('i')}    dl_cmd, timeout=1800, text=True\n"
            f"{m.group('i')})\n"
            f"{m.group('i')}if _cached_audio_proc.returncode != 0:\n"
            f"{m.group('i')}    _cached_audio_error = (_cached_audio_proc.stderr or '')[-500:]\n"
            f"{m.group('i')}    raise RuntimeError(\n"
            f"{m.group('i')}        f\"Кэш аудио yt-dlp rc={{_cached_audio_proc.returncode}}: \"\n"
            f"{m.group('i')}        f\"{{_cached_audio_error or 'unknown error'}}\"\n"
            f"{m.group('i')}    )"
        ),
        expected=1,
        label="cached audio subprocess",
    )

    source = _replace_regex(
        source,
        r"(?P<i>^[ \t]+)proc = await asyncio\.get_running_loop\(\)\.run_in_executor\(\n"
        r"(?P=i)    None, lambda: subprocess\.run\(audio_cmd, capture_output=True, text=True, encoding=\"utf-8\", errors=\"replace\", timeout=600\)\n"
        r"(?P=i)\)",
        lambda m: (
            f"{m.group('i')}proc = await run_cancellable_process(\n"
            f"{m.group('i')}    audio_cmd, timeout=600, text=True\n"
            f"{m.group('i')})"
        ),
        expected=1,
        label="fresh audio subprocess",
    )

    source = _replace_exact(
        source,
        "                    await asyncio.get_running_loop().run_in_executor(\n"
        "                        None, lambda: normalize_mp3_lossless(mp3_path))",
        "                    await await_owned_coroutine(\n"
        "                        asyncio.to_thread(normalize_mp3_lossless, mp3_path)\n"
        "                    )",
        expected=1,
        label="cached mp3gain ownership",
    )
    source = _replace_exact(
        source,
        "                await asyncio.get_running_loop().run_in_executor(\n"
        "                    None, lambda: normalize_mp3_lossless(_mp3_after_dl))",
        "                await await_owned_coroutine(\n"
        "                    asyncio.to_thread(normalize_mp3_lossless, _mp3_after_dl)\n"
        "                )",
        expected=1,
        label="fresh mp3gain ownership",
    )

    recompress_pattern = (
        r"(?P<i>^[ \t]+)(?:proc = )?await asyncio\.get_running_loop\(\)\.run_in_executor\(\n"
        r"(?P=i)    None, lambda: subprocess\.run\(\n"
        r"(?P=i)        \[ffmpeg, \"-i\", str\(mp3_path\), \"-b:a\", \"64k\", \"-y\", str\(mp3_64_path\)\],\n"
        r"(?P=i)        capture_output=True, timeout=300\)?\n"
        r"(?P=i)\)\)?"
    )

    def _recompress_replacement(match: re.Match[str]) -> str:
        indent = match.group("i")
        return (
            f"{indent}mp3_64_path.unlink(missing_ok=True)\n"
            f"{indent}_recompress_proc = await run_cancellable_process(\n"
            f"{indent}    [ffmpeg, \"-i\", str(mp3_path), \"-b:a\", \"64k\", \"-y\", str(mp3_64_path)],\n"
            f"{indent}    timeout=300,\n"
            f"{indent})\n"
            f"{indent}if _recompress_proc.returncode != 0:\n"
            f"{indent}    logger.warning(\n"
            f"{indent}        \"ffmpeg 64k rc=%s: %s\",\n"
            f"{indent}        _recompress_proc.returncode,\n"
            f"{indent}        (_recompress_proc.stderr or b\"\")[-300:],\n"
            f"{indent}    )"
        )

    source = _replace_regex(
        source,
        recompress_pattern,
        _recompress_replacement,
        expected=2,
        label="ffmpeg recompression subprocesses",
    )

    source = _replace_exact(
        source,
        "if mp3_64_path.exists() and mp3_64_path.stat().st_size > 10240:",
        "if (\n"
        "                        _recompress_proc.returncode == 0\n"
        "                        and mp3_64_path.exists()\n"
        "                        and mp3_64_path.stat().st_size > 10240\n"
        "                    ):",
        expected=1,
        label="cached recompression commit gate",
    )
    source = _replace_exact(
        source,
        "if mp3_64_path.exists() and mp3_64_path.stat().st_size > 10240:",
        "if (\n"
        "                    _recompress_proc.returncode == 0\n"
        "                    and mp3_64_path.exists()\n"
        "                    and mp3_64_path.stat().st_size > 10240\n"
        "                ):",
        expected=1,
        label="fresh recompression commit gate",
    )

    ast.parse(source)
    start = source.index("async def process_single_video")
    function = source[start:]
    if "subprocess.run(" in function:
        raise SystemExit("subprocess.run remains in process_single_video")
    if function.count("await run_cancellable_process(") != 7:
        raise SystemExit("unexpected run_cancellable_process call count")
    if function.count("asyncio.to_thread(normalize_mp3_lossless") != 2:
        raise SystemExit("mp3gain ownership postcondition failed")
    if function.count("_recompress_proc.returncode == 0") != 2:
        raise SystemExit("recompression commit gate postcondition failed")

    PATH.write_text(source, encoding="utf-8")
    print(f"patched {PATH}: seven subprocess owners and two thread owners")


if __name__ == "__main__":
    main()
