#!/usr/bin/env python3
"""Make Clips and Montage output files transactional."""
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
        "logger = logging.getLogger(__name__)\n\nasync def render_clip(\n",
        "logger = logging.getLogger(__name__)\n\n\n"
        "def _unlink_render_paths(*paths: Path | None) -> None:\n"
        "    \"\"\"Remove stale/partial render artifacts without masking the main result.\"\"\"\n"
        "    for path in paths:\n"
        "        if path is None:\n"
        "            continue\n"
        "        try:\n"
        "            path.unlink(missing_ok=True)\n"
        "        except OSError as exc:\n"
        "            logger.warning(\"render cleanup failed for %s: %s\", path, exc)\n\n\n"
        "async def render_clip(\n",
        label="render cleanup helper",
    )

    source = _replace_once(
        source,
        "    Возвращает True при успехе.\n"
        "    \"\"\"\n"
        "    try:\n"
        "        ffmpeg = shutil.which(\"ffmpeg\")\n",
        "    Возвращает True при успехе.\n"
        "    \"\"\"\n"
        "    _unlink_render_paths(output_path)\n"
        "    try:\n"
        "        ffmpeg = shutil.which(\"ffmpeg\")\n",
        label="render clip pre-delete",
    )
    source = _replace_once(
        source,
        "            else:\n"
        "                logger.warning(f\"render_clip ffmpeg error: {stderr_tail}\")\n"
        "                return False\n",
        "            else:\n"
        "                logger.warning(f\"render_clip ffmpeg error: {stderr_tail}\")\n"
        "                _unlink_render_paths(output_path)\n"
        "                return False\n",
        label="render clip nonzero cleanup",
    )
    source = _replace_once(
        source,
        "        if not output_path.exists() or output_path.stat().st_size == 0:\n"
        "            logger.warning(\"render_clip: выходной файл не создан или пуст\")\n"
        "            return False\n",
        "        if not output_path.exists() or output_path.stat().st_size == 0:\n"
        "            logger.warning(\"render_clip: выходной файл не создан или пуст\")\n"
        "            _unlink_render_paths(output_path)\n"
        "            return False\n",
        label="render clip postcondition cleanup",
    )
    source = _replace_once(
        source,
        "    except subprocess.TimeoutExpired:\n"
        "        logger.warning(\"render_clip: ffmpeg timeout\")\n"
        "        return False\n"
        "    except Exception as e:\n"
        "        logger.warning(f\"render_clip error: {type(e).__name__}: {e}\")\n"
        "        return False\n",
        "    except asyncio.CancelledError:\n"
        "        _unlink_render_paths(output_path)\n"
        "        raise\n"
        "    except subprocess.TimeoutExpired:\n"
        "        logger.warning(\"render_clip: ffmpeg timeout\")\n"
        "        _unlink_render_paths(output_path)\n"
        "        return False\n"
        "    except Exception as e:\n"
        "        logger.warning(f\"render_clip error: {type(e).__name__}: {e}\")\n"
        "        _unlink_render_paths(output_path)\n"
        "        return False\n",
        label="render clip exception cleanup",
    )

    source = _replace_once(
        source,
        "    Возвращает True при успехе.\n"
        "    \"\"\"\n"
        "    try:\n"
        "        ffmpeg = shutil.which(\"ffmpeg\")\n"
        "        if not ffmpeg or not video_path.exists():\n",
        "    Возвращает True при успехе.\n"
        "    \"\"\"\n"
        "    _unlink_render_paths(snapshot_path)\n"
        "    try:\n"
        "        ffmpeg = shutil.which(\"ffmpeg\")\n"
        "        if not ffmpeg or not video_path.exists():\n",
        label="snapshot pre-delete",
    )
    source = _replace_once(
        source,
        "        if proc.returncode != 0 or not snapshot_path.exists() or snapshot_path.stat().st_size == 0:\n"
        "            logger.warning(f\"create_clip_snapshot: не удалось извлечь кадр из {video_path.name}\")\n"
        "            return False\n",
        "        if proc.returncode != 0 or not snapshot_path.exists() or snapshot_path.stat().st_size == 0:\n"
        "            logger.warning(f\"create_clip_snapshot: не удалось извлечь кадр из {video_path.name}\")\n"
        "            _unlink_render_paths(snapshot_path)\n"
        "            return False\n",
        label="snapshot failure cleanup",
    )
    source = _replace_once(
        source,
        "    except Exception as e:\n"
        "        logger.warning(f\"create_clip_snapshot error: {type(e).__name__}: {e}\")\n"
        "        return False\n",
        "    except asyncio.CancelledError:\n"
        "        _unlink_render_paths(snapshot_path)\n"
        "        raise\n"
        "    except Exception as e:\n"
        "        logger.warning(f\"create_clip_snapshot error: {type(e).__name__}: {e}\")\n"
        "        _unlink_render_paths(snapshot_path)\n"
        "        return False\n",
        label="snapshot exception cleanup",
    )

    source = _replace_once(
        source,
        "    temp_parts: list[Path] = []\n"
        "    concat_list_path: Path | None = None\n"
        "    try:\n",
        "    temp_parts: list[Path] = []\n"
        "    concat_list_path: Path | None = None\n"
        "    _unlink_render_paths(output_path)\n"
        "    try:\n",
        label="montage final pre-delete",
    )
    source = _replace_once(
        source,
        "            part_path = output_path.parent / f\"{output_path.stem}_part{i}.mp4\"\n"
        "            temp_parts.append(part_path)\n",
        "            part_path = output_path.parent / f\"{output_path.stem}_part{i}.mp4\"\n"
        "            _unlink_render_paths(part_path)\n"
        "            temp_parts.append(part_path)\n",
        label="montage part pre-delete",
    )
    source = _replace_once(
        source,
        "            if proc.returncode != 0 or not part_path.exists():\n"
        "                logger.warning(f\"Montage: фрагмент {i} не отрендерен\")\n"
        "                for p in temp_parts: p.unlink(missing_ok=True)\n"
        "                return False\n",
        "            if (\n"
        "                proc.returncode != 0\n"
        "                or not part_path.exists()\n"
        "                or part_path.stat().st_size == 0\n"
        "            ):\n"
        "                logger.warning(f\"Montage: фрагмент {i} не отрендерен\")\n"
        "                _unlink_render_paths(*temp_parts, concat_list_path, output_path)\n"
        "                return False\n",
        label="montage part failure cleanup",
    )
    source = _replace_once(
        source,
        "        if len(existing_parts) < 2:\n"
        "            for p in temp_parts: p.unlink(missing_ok=True)\n"
        "            return False\n\n"
        "        concat_list_path = output_path.parent / f\"{output_path.stem}_concat.txt\"\n"
        "        with open(concat_list_path, \"w\", encoding=\"utf-8\") as f:\n",
        "        if len(existing_parts) < 2:\n"
        "            _unlink_render_paths(*temp_parts, concat_list_path, output_path)\n"
        "            return False\n\n"
        "        concat_list_path = output_path.parent / f\"{output_path.stem}_concat.txt\"\n"
        "        _unlink_render_paths(concat_list_path)\n"
        "        with open(concat_list_path, \"w\", encoding=\"utf-8\") as f:\n",
        label="montage concat pre-delete",
    )
    source = _replace_once(
        source,
        "        for p in temp_parts: p.unlink(missing_ok=True)\n"
        "        concat_list_path.unlink(missing_ok=True)\n\n"
        "        if proc.returncode != 0 or not output_path.exists():\n"
        "            logger.warning(f\"Montage: concat failed: {(proc.stderr or '')[-300:]}\")\n"
        "            return False\n",
        "        _unlink_render_paths(*temp_parts, concat_list_path)\n\n"
        "        if (\n"
        "            proc.returncode != 0\n"
        "            or not output_path.exists()\n"
        "            or output_path.stat().st_size == 0\n"
        "        ):\n"
        "            logger.warning(f\"Montage: concat failed: {(proc.stderr or '')[-300:]}\")\n"
        "            _unlink_render_paths(output_path)\n"
        "            return False\n",
        label="montage concat cleanup",
    )
    source = _replace_once(
        source,
        "    except subprocess.TimeoutExpired:\n"
        "        logger.warning(\"render_montage_short: ffmpeg timeout\")\n"
        "        for p in temp_parts:\n"
        "            try: p.unlink(missing_ok=True)\n"
        "            except Exception: pass\n"
        "        if concat_list_path:\n"
        "            try: concat_list_path.unlink(missing_ok=True)\n"
        "            except Exception: pass\n"
        "        return False\n"
        "    except Exception as e:\n"
        "        logger.warning(f\"render_montage_short error: {type(e).__name__}: {e}\")\n"
        "        for p in temp_parts:\n"
        "            try: p.unlink(missing_ok=True)\n"
        "            except Exception: pass\n"
        "        if concat_list_path:\n"
        "            try: concat_list_path.unlink(missing_ok=True)\n"
        "            except Exception: pass\n"
        "        return False\n",
        "    except asyncio.CancelledError:\n"
        "        _unlink_render_paths(*temp_parts, concat_list_path, output_path)\n"
        "        raise\n"
        "    except subprocess.TimeoutExpired:\n"
        "        logger.warning(\"render_montage_short: ffmpeg timeout\")\n"
        "        _unlink_render_paths(*temp_parts, concat_list_path, output_path)\n"
        "        return False\n"
        "    except Exception as e:\n"
        "        logger.warning(f\"render_montage_short error: {type(e).__name__}: {e}\")\n"
        "        _unlink_render_paths(*temp_parts, concat_list_path, output_path)\n"
        "        return False\n",
        label="montage exception cleanup",
    )

    ast.parse(source)
    if source.count("_unlink_render_paths(output_path)") < 7:
        raise SystemExit("output cleanup coverage changed")
    if source.count("except asyncio.CancelledError:") < 3:
        raise SystemExit("cancellation cleanup coverage changed")
    if "for p in temp_parts: p.unlink" in source:
        raise SystemExit("legacy montage cleanup remains")

    PATH.write_text(source, encoding="utf-8")
    print("made Clips, snapshots, and Montage outputs transactional")


if __name__ == "__main__":
    main()
