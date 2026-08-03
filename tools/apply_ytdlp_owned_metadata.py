#!/usr/bin/env python3
"""One-time branch patch for in-process yt-dlp metadata ownership."""
from __future__ import annotations

import ast
from pathlib import Path


PATH = Path("pipelines/main_pipeline.py")
IMPORT_ANCHOR = (
    "from services.ffmpeg import YTDLP_BASE_ARGS                   "
    "# FIX #23: нужен результат, не функция\n"
)
IMPORT_LINE = "from services.async_worker import await_owned_with_soft_timeout\n"
OLD_BLOCK = '''    try:
        info = await asyncio.wait_for(
            asyncio.get_running_loop().run_in_executor(None, _run),
            timeout=timeout,
        )
    except Exception as e:
        logger.info(
            "yt-dlp in-process info не удался (%s) — откат на subprocess",
            str(e)[:200],
        )
        return None
'''
NEW_BLOCK = '''    try:
        info, deadline_exceeded = await await_owned_with_soft_timeout(
            asyncio.to_thread(_run),
            timeout=timeout,
        )
        if deadline_exceeded:
            logger.warning(
                "yt-dlp in-process превысил мягкий лимит %ss, "
                "но завершился; использую поздний результат без "
                "параллельного subprocess-fallback",
                timeout,
            )
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.info(
            "yt-dlp in-process info не удался (%s) — откат на subprocess",
            str(e)[:200],
        )
        return None
'''


def main() -> None:
    source = PATH.read_text(encoding="utf-8")
    if IMPORT_LINE not in source:
        if source.count(IMPORT_ANCHOR) != 1:
            raise SystemExit("unique import anchor not found")
        source = source.replace(IMPORT_ANCHOR, IMPORT_ANCHOR + IMPORT_LINE, 1)
    if source.count(OLD_BLOCK) != 1:
        raise SystemExit("exact legacy metadata timeout block not found")
    source = source.replace(OLD_BLOCK, NEW_BLOCK, 1)
    ast.parse(source)

    function_start = source.index("async def _ytdlp_info_inprocess")
    function_end = source.index("\n\nasync def process_single_video", function_start)
    function = source[function_start:function_end]
    required = (
        "await await_owned_with_soft_timeout(",
        "asyncio.to_thread(_run)",
        "использую поздний результат",
    )
    if not all(marker in function for marker in required):
        raise SystemExit("metadata ownership postcondition failed")
    if "run_in_executor" in function or "asyncio.wait_for" in function:
        raise SystemExit("orphaning metadata executor pattern remains")

    PATH.write_text(source, encoding="utf-8")
    print(f"patched {PATH}: owned soft metadata deadline")


if __name__ == "__main__":
    main()
