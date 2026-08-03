#!/usr/bin/env python3
"""One-time branch patch: add ownership wrappers without rewriting legacy bodies."""
from __future__ import annotations

import ast
from pathlib import Path


PATH = Path("services/shorts_video.py")
PUBLIC_NAMES = (
    "download_video_for_shorts",
    "render_short_clip",
    "postprocess_short",
    "transcribe_short_clip",
    "burn_subtitles_into_short",
    "create_short_title_poster",
    "create_short_snapshot",
)


def _rename_top_level_async_functions(source: str) -> str:
    tree = ast.parse(source)
    nodes = {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name in PUBLIC_NAMES
    }
    missing = [name for name in PUBLIC_NAMES if name not in nodes]
    if missing:
        raise SystemExit(f"missing expected async functions: {missing}")

    lines = source.splitlines(keepends=True)
    for name, node in sorted(nodes.items(), key=lambda item: item[1].lineno, reverse=True):
        index = node.lineno - 1
        expected = f"async def {name}("
        if expected not in lines[index]:
            raise SystemExit(f"unexpected declaration for {name}: {lines[index]!r}")
        lines[index] = lines[index].replace(
            expected,
            f"async def _unowned_{name}(",
            1,
        )
    return "".join(lines)


def _insert_import(source: str) -> str:
    import_line = "from services.async_worker import await_owned_coroutine\n"
    if import_line in source:
        return source
    lines = source.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if line.startswith("from services.ffmpeg import "):
            lines.insert(index + 1, import_line)
            return "".join(lines)
    raise SystemExit("services.ffmpeg import anchor not found")


WRAPPERS = r'''

# ─── Cancellation ownership boundary for legacy executor work ────────────────
#
# The implementations above predate the shared asyncio process owner and still
# contain a few bounded ``run_in_executor`` calls. Cancelling their asyncio
# Future cannot stop native thread work. Public callers therefore use a shielded
# ownership boundary: the inner operation and its semaphore/temp-file cleanup
# finish first, then caller cancellation is propagated.

async def download_video_for_shorts(
    url: str,
    media_id: str,
    workdir: Optional[Path] = None,
) -> Optional[Path]:
    return await await_owned_coroutine(
        _unowned_download_video_for_shorts(url, media_id, workdir=workdir)
    )


async def render_short_clip(
    source_video_path: Path,
    output_path: Path,
    start_seconds: int,
    end_seconds: int,
    *,
    visual_mode: str = "full_frame_vertical",
) -> bool:
    return await await_owned_coroutine(
        _unowned_render_short_clip(
            source_video_path,
            output_path,
            start_seconds,
            end_seconds,
            visual_mode=visual_mode,
        )
    )


async def postprocess_short(
    input_path: Path,
    output_path: Path,
    *,
    normalize_audio: bool = True,
    speed: float = 1.0,
) -> bool:
    return await await_owned_coroutine(
        _unowned_postprocess_short(
            input_path,
            output_path,
            normalize_audio=normalize_audio,
            speed=speed,
        )
    )


async def transcribe_short_clip(
    video_path: Path,
    ai_data: dict = None,
) -> list[dict]:
    return await await_owned_coroutine(
        _unowned_transcribe_short_clip(video_path, ai_data=ai_data)
    )


async def burn_subtitles_into_short(
    input_path: Path,
    output_path: Path,
    segments: list[dict],
) -> bool:
    # Preserve compatibility for old imports while enforcing the single active
    # transactional ASS/process owner introduced by PR #85/#94.
    from services.shorts_subtitle_burn import (
        burn_subtitles_into_short as transactional_burn,
    )

    return await transactional_burn(input_path, output_path, segments)


async def create_short_title_poster(
    video_path: Path,
    poster_path: Path,
    title: str,
    clip_duration_seconds: float,
) -> bool:
    return await await_owned_coroutine(
        _unowned_create_short_title_poster(
            video_path,
            poster_path,
            title,
            clip_duration_seconds,
        )
    )


async def create_short_snapshot(
    video_path: Path,
    snapshot_path: Path,
    clip_duration_seconds: float,
) -> bool:
    return await await_owned_coroutine(
        _unowned_create_short_snapshot(
            video_path,
            snapshot_path,
            clip_duration_seconds,
        )
    )
'''


def _insert_wrappers(source: str) -> str:
    if "Cancellation ownership boundary for legacy executor work" in source:
        raise SystemExit("ownership wrappers already present")
    marker = "\n# ─── Clips MVP (длинные фрагменты 5–15 мин) ──────────────────\n"
    if source.count(marker) != 1:
        raise SystemExit("unique Clips marker not found")
    return source.replace(marker, WRAPPERS + marker, 1)


def main() -> None:
    source = PATH.read_text(encoding="utf-8")
    source = _rename_top_level_async_functions(source)
    source = _insert_import(source)
    source = _insert_wrappers(source)
    ast.parse(source)

    for name in PUBLIC_NAMES:
        if source.count(f"async def {name}(") != 1:
            raise SystemExit(f"public wrapper count invalid for {name}")
        if source.count(f"async def _unowned_{name}(") != 1:
            raise SystemExit(f"legacy implementation count invalid for {name}")

    PATH.write_text(source, encoding="utf-8")
    print(f"patched {PATH}: {len(PUBLIC_NAMES)} ownership boundaries")


if __name__ == "__main__":
    main()
