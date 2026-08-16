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


def remove_runtime_feature(text: str, feature_id: str) -> str:
    quoted = r"[\"']" + re.escape(feature_id) + r"[\"']"
    pattern = re.compile(r"\n    RuntimeFeature\(\n        " + quoted + r",.*?\n    \),", re.DOTALL)
    text2, count = pattern.subn("", text, count=1)
    if count != 1:
        raise RuntimeError(f"runtime manifest feature not found exactly once: {feature_id}")
    print(f"removed runtime manifest feature: {feature_id}")
    return text2


def wave2() -> None:
    path = "pipelines/main_pipeline.py"
    text = read(path)

    import_anchor = "from core.timestamp_quality import timestamp_coverage_ratio\n"
    imports = (
        "from core.timestamp_quality import timestamp_coverage_ratio\n"
        "from core.bounded_cache import BoundedLRUDict\n"
        "from services.mp3_conversion import reencode_mp3_64k_atomic\n"
    )
    if import_anchor not in text:
        raise RuntimeError("main_pipeline import anchor missing")
    text = text.replace(import_anchor, imports, 1)

    cache_old = "_LIVEDUB_TITLE_CACHE: dict[tuple[str, str, str], tuple[str, str]] = {}\n"
    cache_new = '''try:\n    _LIVEDUB_TITLE_CACHE_MAX = int(\n        os.getenv("LIVEDUB_TITLE_CACHE_MAX", "256").strip() or "256"\n    )\nexcept ValueError:\n    _LIVEDUB_TITLE_CACHE_MAX = 256\n_LIVEDUB_TITLE_CACHE: BoundedLRUDict = BoundedLRUDict(\n    max_entries=_LIVEDUB_TITLE_CACHE_MAX\n)\n\n\nasync def _run_optional_stage(name: str, awaitable, default=False):\n    """Isolate optional publication stages without changing imported bindings."""\n    try:\n        return await awaitable\n    except asyncio.CancelledError:\n        raise\n    except Exception as exc:\n        logger.error(\n            "Optional stage %s failed but pipeline continues: %s",\n            name,\n            exc,\n            exc_info=True,\n        )\n        return default\n'''
    if cache_old not in text:
        raise RuntimeError("main_pipeline title-cache anchor missing")
    text = text.replace(cache_old, cache_new, 1)

    block1 = '''                mp3_64_path = DOWNLOAD_DIR / f"{media_id}_64.mp3"\n                ffmpeg = shutil.which("ffmpeg")\n                # AUDIT R39: не пере-сжимаем сам в себя (вход==выход) — потеря файла.\n                if ffmpeg and mp3_path.name != mp3_64_path.name:\n                    mp3_64_path.unlink(missing_ok=True)\n                    _recompress_proc = await run_cancellable_process(\n                        [ffmpeg, "-i", str(mp3_path), "-b:a", "64k", "-y", str(mp3_64_path)],\n                        timeout=300,\n                    )\n                    if _recompress_proc.returncode != 0:\n                        logger.warning(\n                            "ffmpeg 64k rc=%s: %s",\n                            _recompress_proc.returncode,\n                            (_recompress_proc.stderr or b"")[-300:],\n                        )\n                    # FIX: verify re-encoded file is not empty/corrupt\n                    if (\n                        _recompress_proc.returncode == 0\n                        and mp3_64_path.exists()\n                        and mp3_64_path.stat().st_size > 10240\n                    ):\n                        mp3_path.unlink(missing_ok=True)\n                        mp3_path = mp3_64_path\n                        file_size_mb = mp3_path.stat().st_size / (1024 * 1024)\n                        bitrate = "64"\n                    elif mp3_64_path.exists():\n                        mp3_64_path.unlink(missing_ok=True)\n'''
    block1_new = '''                mp3_64_path = DOWNLOAD_DIR / f"{media_id}_64.mp3"\n                if mp3_path.name != mp3_64_path.name:\n                    converted = await reencode_mp3_64k_atomic(mp3_path, mp3_64_path)\n                    if converted:\n                        mp3_path.unlink(missing_ok=True)\n                        mp3_path = mp3_64_path\n                        file_size_mb = mp3_path.stat().st_size / (1024 * 1024)\n                        bitrate = "64"\n                    else:\n                        logger.warning("Verified atomic MP3 64k conversion failed: %s", mp3_path.name)\n'''
    if block1 not in text:
        raise RuntimeError("first MP3 recompress block anchor missing")
    text = text.replace(block1, block1_new, 1)

    block2 = '''            mp3_64_path = DOWNLOAD_DIR / f"{media_id}_64.mp3"\n            # Re-encode existing mp3 via ffmpeg directly\n            ffmpeg = shutil.which("ffmpeg")\n            # AUDIT R39: если переиспользованный из кэша файл — УЖЕ {media_id}_64.mp3,\n            # то вход==выход: `ffmpeg -i X … X` испортит/обнулит единственный файл\n            # (а ниже unlink удалил бы оригинал → потеря аудио + краш stat()). Он и\n            # так 64 kbps — повторное сжатие бессмысленно, пропускаем к «слишком большой».\n            if ffmpeg and mp3_path.name != mp3_64_path.name:\n                mp3_64_path.unlink(missing_ok=True)\n                _recompress_proc = await run_cancellable_process(\n                    [ffmpeg, "-i", str(mp3_path), "-b:a", "64k", "-y", str(mp3_64_path)],\n                    timeout=300,\n                )\n                if _recompress_proc.returncode != 0:\n                    logger.warning(\n                        "ffmpeg 64k rc=%s: %s",\n                        _recompress_proc.returncode,\n                        (_recompress_proc.stderr or b"")[-300:],\n                    )\n                # FIX: verify re-encoded file is not empty/corrupt before\n                # deleting the good original. ffmpeg can create 0-byte output\n                # on disk errors or corrupt input.\n                if (\n                    _recompress_proc.returncode == 0\n                    and mp3_64_path.exists()\n                    and mp3_64_path.stat().st_size > 10240\n                ):\n                    mp3_path.unlink(missing_ok=True)\n                    mp3_path = mp3_64_path\n                    file_size_mb = mp3_path.stat().st_size / (1024 * 1024)\n                elif mp3_64_path.exists():\n                    mp3_64_path.unlink(missing_ok=True)  # remove corrupt output\n'''
    block2_new = '''            mp3_64_path = DOWNLOAD_DIR / f"{media_id}_64.mp3"\n            if mp3_path.name != mp3_64_path.name:\n                converted = await reencode_mp3_64k_atomic(mp3_path, mp3_64_path)\n                if converted:\n                    mp3_path.unlink(missing_ok=True)\n                    mp3_path = mp3_64_path\n                    file_size_mb = mp3_path.stat().st_size / (1024 * 1024)\n                else:\n                    logger.warning("Verified atomic MP3 64k conversion failed: %s", mp3_path.name)\n'''
    if block2 not in text:
        raise RuntimeError("second MP3 recompress block anchor missing")
    text = text.replace(block2, block2_new, 1)

    replacements = [
        (
'''            await process_and_send_shorts(\n                url=url,\n                media_id=media_id,\n                mp3_path=mp3_path,\n                title=title,\n                performer=performer,\n                duration=duration,\n                ai_data=ai_data,\n                update=update,\n                existing_audio_part=used_audio_part,   # ← REUSE\n                existing_client=used_client,            # ← REUSE\n                rutube_url=rutube_url,\n                vk_url=vk_url,\n                workdir=ld_work if 'ld_work' in locals() else None,\n                livedub_video_path=_shorts_livedub_path,\n            )\n''',
'''            await _run_optional_stage(\n                "process_and_send_shorts",\n                process_and_send_shorts(\n                    url=url,\n                    media_id=media_id,\n                    mp3_path=mp3_path,\n                    title=title,\n                    performer=performer,\n                    duration=duration,\n                    ai_data=ai_data,\n                    update=update,\n                    existing_audio_part=used_audio_part,\n                    existing_client=used_client,\n                    rutube_url=rutube_url,\n                    vk_url=vk_url,\n                    workdir=ld_work if 'ld_work' in locals() else None,\n                    livedub_video_path=_shorts_livedub_path,\n                ),\n                False,\n            )\n'''),
        (
'''            await process_and_send_clips(\n                url=url,\n                media_id=media_id,\n                mp3_path=mp3_path,\n                title=title,\n                performer=performer,\n                duration=duration,\n                ai_data=ai_data,\n                update=update,\n                existing_audio_part=used_audio_part,   # ← REUSE\n                existing_client=used_client,            # ← REUSE\n                rutube_url=rutube_url,\n                vk_url=vk_url,\n                livedub_video_path=_shorts_livedub_path,\n            )\n''',
'''            await _run_optional_stage(\n                "process_and_send_clips",\n                process_and_send_clips(\n                    url=url,\n                    media_id=media_id,\n                    mp3_path=mp3_path,\n                    title=title,\n                    performer=performer,\n                    duration=duration,\n                    ai_data=ai_data,\n                    update=update,\n                    existing_audio_part=used_audio_part,\n                    existing_client=used_client,\n                    rutube_url=rutube_url,\n                    vk_url=vk_url,\n                    livedub_video_path=_shorts_livedub_path,\n                ),\n                False,\n            )\n'''),
        (
'''            _prefetched_extras = await create_extras_candidates(\n                ai_data=ai_data,\n                title=title,\n                performer=performer,\n                duration=duration,\n            )\n''',
'''            _prefetched_extras = await _run_optional_stage(\n                "create_extras_candidates",\n                create_extras_candidates(\n                    ai_data=ai_data,\n                    title=title,\n                    performer=performer,\n                    duration=duration,\n                ),\n                {"montage_candidates": [], "highlights_candidates": []},\n            )\n'''),
        (
'''            await process_and_send_montage(\n                url=url, media_id=media_id, mp3_path=mp3_path,\n                title=title, performer=performer, duration=duration,\n                ai_data=ai_data, update=update,\n                rutube_url=rutube_url, vk_url=vk_url,\n                prefetched_candidates=_prefetched_extras.get("montage_candidates", []),\n                livedub_video_path=_shorts_livedub_path,\n            )\n''',
'''            await _run_optional_stage(\n                "process_and_send_montage",\n                process_and_send_montage(\n                    url=url, media_id=media_id, mp3_path=mp3_path,\n                    title=title, performer=performer, duration=duration,\n                    ai_data=ai_data, update=update,\n                    rutube_url=rutube_url, vk_url=vk_url,\n                    prefetched_candidates=_prefetched_extras.get("montage_candidates", []),\n                    livedub_video_path=_shorts_livedub_path,\n                ),\n                False,\n            )\n'''),
        (
'''            await process_and_send_highlights(\n                url=url, media_id=media_id, mp3_path=mp3_path,\n                title=title, performer=performer, duration=duration,\n                ai_data=ai_data, update=update,\n                rutube_url=rutube_url, vk_url=vk_url,\n                prefetched_candidates=_prefetched_extras.get("highlights_candidates", []),\n                livedub_video_path=_shorts_livedub_path,\n            )\n''',
'''            await _run_optional_stage(\n                "process_and_send_highlights",\n                process_and_send_highlights(\n                    url=url, media_id=media_id, mp3_path=mp3_path,\n                    title=title, performer=performer, duration=duration,\n                    ai_data=ai_data, update=update,\n                    rutube_url=rutube_url, vk_url=vk_url,\n                    prefetched_candidates=_prefetched_extras.get("highlights_candidates", []),\n                    livedub_video_path=_shorts_livedub_path,\n                ),\n                False,\n            )\n'''),
    ]
    for old, new in replacements:
        if old not in text:
            raise RuntimeError("optional-stage anchor missing: " + old.splitlines()[0])
        text = text.replace(old, new, 1)

    write(path, text)

    manifest = read("services/runtime_manifest.py")
    manifest = remove_runtime_feature(manifest, "project-runtime-hardening")
    write("services/runtime_manifest.py", manifest)

    hardening = ROOT / "services/project_runtime_hardening.py"
    if not hardening.exists():
        raise RuntimeError("project_runtime_hardening.py unexpectedly missing")
    hardening.unlink()
    print("deleted services/project_runtime_hardening.py")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("wave", choices=("wave2",))
    args = parser.parse_args()
    wave2()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
