#!/usr/bin/env python3
"""Make active Shorts render, transform, poster and snapshot outputs transactional."""
from __future__ import annotations

import ast
from collections.abc import Callable
from pathlib import Path


PATH = Path("services/shorts_video.py")


def _replace_once(source: str, old: str, new: str, *, label: str) -> str:
    count = source.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    return source.replace(old, new, 1)


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


def _patch_render(function: str) -> str:
    function = _replace_once(
        function,
        "    try:\n        ffmpeg = shutil.which(\"ffmpeg\")\n",
        "    _unlink_short_paths(output_path, protected=(source_video_path,))\n"
        "    try:\n"
        "        ffmpeg = shutil.which(\"ffmpeg\")\n",
        label="Short render pre-delete",
    )
    function = _replace_once(
        function,
        "        if proc.returncode != 0:\n"
        "            logger.warning(f\"render_short_clip ffmpeg error: {(proc.stderr or '')[-500:]}\")\n"
        "            return False\n",
        "        if proc.returncode != 0:\n"
        "            logger.warning(f\"render_short_clip ffmpeg error: {(proc.stderr or '')[-500:]}\")\n"
        "            _unlink_short_paths(output_path, protected=(source_video_path,))\n"
        "            return False\n",
        label="Short render nonzero cleanup",
    )
    return _replace_once(
        function,
        "    except subprocess.TimeoutExpired:\n"
        "        logger.warning(\"render_short_clip: ffmpeg timeout\")\n"
        "        return False\n"
        "    except Exception as e:\n"
        "        logger.warning(f\"render_short_clip error: {type(e).__name__}: {e}\")\n"
        "        return False\n",
        "    except asyncio.CancelledError:\n"
        "        _unlink_short_paths(output_path, protected=(source_video_path,))\n"
        "        raise\n"
        "    except subprocess.TimeoutExpired:\n"
        "        logger.warning(\"render_short_clip: ffmpeg timeout\")\n"
        "        _unlink_short_paths(output_path, protected=(source_video_path,))\n"
        "        return False\n"
        "    except Exception as e:\n"
        "        logger.warning(f\"render_short_clip error: {type(e).__name__}: {e}\")\n"
        "        _unlink_short_paths(output_path, protected=(source_video_path,))\n"
        "        return False\n",
        label="Short render exception cleanup",
    )


def _patch_transform(function: str) -> str:
    function = _replace_once(
        function,
        "    try:\n        ffmpeg = shutil.which(\"ffmpeg\")\n",
        "    _unlink_short_paths(output_path, protected=(input_path,))\n"
        "    try:\n"
        "        ffmpeg = shutil.which(\"ffmpeg\")\n",
        label="Short transform pre-delete",
    )
    function = _replace_once(
        function,
        "        if not video_filters and not audio_filters:\n"
        "            shutil.copy2(input_path, output_path)\n"
        "            return True\n",
        "        if not video_filters and not audio_filters:\n"
        "            if _same_short_path(input_path, output_path):\n"
        "                return input_path.exists() and input_path.stat().st_size > 0\n"
        "            shutil.copy2(input_path, output_path)\n"
        "            return output_path.exists() and output_path.stat().st_size > 0\n",
        label="Short transform same-path no-op",
    )
    function = _replace_once(
        function,
        "        if proc.returncode != 0:\n"
        "            logger.warning(f\"postprocess_short ffmpeg error: {(proc.stderr or '')[-500:]}\")\n"
        "            return False\n"
        "        if not output_path.exists() or output_path.stat().st_size == 0:\n"
        "            return False\n",
        "        if proc.returncode != 0:\n"
        "            logger.warning(f\"postprocess_short ffmpeg error: {(proc.stderr or '')[-500:]}\")\n"
        "            _unlink_short_paths(output_path, protected=(input_path,))\n"
        "            return False\n"
        "        if not output_path.exists() or output_path.stat().st_size == 0:\n"
        "            _unlink_short_paths(output_path, protected=(input_path,))\n"
        "            return False\n",
        label="Short transform failure cleanup",
    )
    return _replace_once(
        function,
        "    except subprocess.TimeoutExpired:\n"
        "        logger.warning(\"postprocess_short: ffmpeg timeout\")\n"
        "        return False\n"
        "    except Exception as e:\n"
        "        logger.warning(f\"postprocess_short error: {type(e).__name__}: {e}\")\n"
        "        return False\n",
        "    except asyncio.CancelledError:\n"
        "        _unlink_short_paths(output_path, protected=(input_path,))\n"
        "        raise\n"
        "    except subprocess.TimeoutExpired:\n"
        "        logger.warning(\"postprocess_short: ffmpeg timeout\")\n"
        "        _unlink_short_paths(output_path, protected=(input_path,))\n"
        "        return False\n"
        "    except Exception as e:\n"
        "        logger.warning(f\"postprocess_short error: {type(e).__name__}: {e}\")\n"
        "        _unlink_short_paths(output_path, protected=(input_path,))\n"
        "        return False\n",
        label="Short transform exception cleanup",
    )


def _patch_poster(function: str) -> str:
    function = _replace_once(
        function,
        "    if not HAS_PILLOW:\n        return False\n",
        "    _unlink_short_paths(poster_path, protected=(video_path,))\n"
        "    if not HAS_PILLOW:\n"
        "        return False\n",
        label="Short poster pre-delete",
    )
    function = _replace_once(
        function,
        "        if result and poster_path.exists() and poster_path.stat().st_size > 0:\n"
        "            logger.info(f\"Title poster: {poster_path.name}\")\n"
        "            return True\n"
        "        return False\n",
        "        if result and poster_path.exists() and poster_path.stat().st_size > 0:\n"
        "            logger.info(f\"Title poster: {poster_path.name}\")\n"
        "            return True\n"
        "        _unlink_short_paths(poster_path, protected=(video_path,))\n"
        "        return False\n",
        label="Short poster result cleanup",
    )
    return _replace_once(
        function,
        "    except Exception as e:\n"
        "        logger.warning(f\"create_short_title_poster error: {type(e).__name__}: {e}\")\n"
        "        if frame_path is not None:\n"
        "            try:\n"
        "                frame_path.unlink(missing_ok=True)\n"
        "            except Exception:\n"
        "                pass\n"
        "        return False\n",
        "    except asyncio.CancelledError:\n"
        "        _unlink_short_paths(frame_path, poster_path, protected=(video_path,))\n"
        "        raise\n"
        "    except Exception as e:\n"
        "        logger.warning(f\"create_short_title_poster error: {type(e).__name__}: {e}\")\n"
        "        _unlink_short_paths(frame_path, poster_path, protected=(video_path,))\n"
        "        return False\n",
        label="Short poster exception cleanup",
    )


def _patch_snapshot(function: str) -> str:
    function = _replace_once(
        function,
        "    try:\n        ffmpeg = shutil.which(\"ffmpeg\")\n",
        "    _unlink_short_paths(snapshot_path, protected=(video_path,))\n"
        "    try:\n"
        "        ffmpeg = shutil.which(\"ffmpeg\")\n",
        label="Short snapshot pre-delete",
    )
    function = _replace_once(
        function,
        "        if proc.returncode != 0 or not snapshot_path.exists() or snapshot_path.stat().st_size == 0:\n"
        "            logger.warning(f\"create_short_snapshot: не удалось извлечь кадр из {video_path.name}\")\n"
        "            return False\n",
        "        if proc.returncode != 0 or not snapshot_path.exists() or snapshot_path.stat().st_size == 0:\n"
        "            logger.warning(f\"create_short_snapshot: не удалось извлечь кадр из {video_path.name}\")\n"
        "            _unlink_short_paths(snapshot_path, protected=(video_path,))\n"
        "            return False\n",
        label="Short snapshot failure cleanup",
    )
    return _replace_once(
        function,
        "    except Exception as e:\n"
        "        logger.warning(f\"create_short_snapshot error: {type(e).__name__}: {e}\")\n"
        "        return False\n",
        "    except asyncio.CancelledError:\n"
        "        _unlink_short_paths(snapshot_path, protected=(video_path,))\n"
        "        raise\n"
        "    except Exception as e:\n"
        "        logger.warning(f\"create_short_snapshot error: {type(e).__name__}: {e}\")\n"
        "        _unlink_short_paths(snapshot_path, protected=(video_path,))\n"
        "        return False\n",
        label="Short snapshot exception cleanup",
    )


def main() -> None:
    source = PATH.read_text(encoding="utf-8")
    source = _replace_once(
        source,
        "logger = logging.getLogger(__name__)\n",
        "logger = logging.getLogger(__name__)\n\n\n"
        "def _same_short_path(left: Path, right: Path) -> bool:\n"
        "    try:\n"
        "        return left.resolve(strict=False) == right.resolve(strict=False)\n"
        "    except OSError:\n"
        "        return left.absolute() == right.absolute()\n\n\n"
        "def _unlink_short_paths(\n"
        "    *paths: Path | None,\n"
        "    protected: tuple[Path, ...] = (),\n"
        ") -> None:\n"
        "    \"\"\"Remove stale/partial outputs without deleting protected inputs.\"\"\"\n"
        "    for path in paths:\n"
        "        if path is None:\n"
        "            continue\n"
        "        if any(_same_short_path(path, keep) for keep in protected):\n"
        "            continue\n"
        "        try:\n"
        "            path.unlink(missing_ok=True)\n"
        "        except OSError as exc:\n"
        "            logger.warning(\"Shorts cleanup failed for %s: %s\", path, exc)\n",
        label="Short output cleanup helpers",
    )
    source = _transform(source, "_unowned_render_short_clip", _patch_render)
    source = _transform(source, "_unowned_short_transform", _patch_transform)
    source = _transform(source, "_unowned_create_short_title_poster", _patch_poster)
    source = _transform(source, "_unowned_create_short_snapshot", _patch_snapshot)

    ast.parse(source)
    selected = "\n".join(
        source[_function_bounds(source, name)[0] : _function_bounds(source, name)[1]]
        for name in (
            "_unowned_render_short_clip",
            "_unowned_short_transform",
            "_unowned_create_short_title_poster",
            "_unowned_create_short_snapshot",
        )
    )
    if selected.count("except asyncio.CancelledError:") != 4:
        raise SystemExit("Short cancellation cleanup count changed")
    if selected.count("_unlink_short_paths(") < 14:
        raise SystemExit("Short transactional cleanup coverage changed")
    if "_same_short_path(input_path, output_path)" not in selected:
        raise SystemExit("same-path transform protection missing")

    PATH.write_text(source, encoding="utf-8")
    print("made active Shorts outputs transactional")


if __name__ == "__main__":
    main()
