#!/usr/bin/env python3
"""
Playlist Pipeline — handle_playlist.

AUDIT FIX M2: check_rate_limit вызывается ВНУТРИ цикла плейлиста
              (раньше один раз перед стартом — позволяло выкачать весь дневной
               лимит за один заход).
AUDIT FIX M3: ошибки отдельных видео не спамят пользователю — собираются
              в список и показываются в финальном сообщении.
AUDIT FIX L11: media_url вычисляется через extract_url или собирается с учётом
               domain (RuTube/VK тоже работают через yt-dlp).
AUDIT FIX L12: PLAYLIST_DELAY_SEC конфигурируется через .env.
"""
import asyncio
import logging
import os
import yt_dlp

from core.globals import GEMINI_CLIENTS, _get_video_lock, _release_video_lock
from core.database import (
    MAX_PLAYLIST_SIZE,
    areserve_rate_limit,
    WHITELIST_IDS,
)
from services.ffmpeg import COOKIES_FILE, _proxy_for_ytdlp, _firefox_cookie_source_available
from pipelines.main_pipeline import process_single_video
from core.progress import safe_edit_text
from core.utils import mask_api_key as _mask

logger = logging.getLogger(__name__)

# AUDIT L12: задержка между видео в плейлисте — конфигурируется
PLAYLIST_DELAY_SEC = float(os.getenv("PLAYLIST_DELAY_SEC", "1.0"))


def _build_media_url(entry: dict) -> str | None:
    """AUDIT L11: учитываем разные платформы вместо хардкода YouTube."""
    # 1) Если есть готовый url — используем его
    explicit = entry.get("url") or entry.get("webpage_url")
    if explicit and explicit.startswith("http"):
        return explicit
    # 2) Иначе пытаемся восстановить по ie_key + id
    vid = entry.get("id")
    if not vid:
        return None
    ie = (entry.get("ie_key") or "").lower()
    if "rutube" in ie:
        return f"https://rutube.ru/video/{vid}/"
    if "vk" in ie:
        return f"https://vkvideo.ru/video{vid}"
    # 3) По умолчанию — YouTube (исторический fallback)
    return f"https://www.youtube.com/watch?v={vid}"


async def handle_playlist(url, update, context, user_id: int = 0):
    status_msg = await update.message.reply_text("📋 Загружаю плейлист...")
    failed_entries: list[str] = []  # AUDIT M3: собираем упавшие
    try:
        playlist_opts = {
            "extractor_args": {"youtube": {"player_client": ["web"]}},
            "sleep_interval": 2,
            "quiet": True,
            "extract_flat": True,
            "socket_timeout": 30,
            "retries": 3,
        }
        if COOKIES_FILE.exists():
            playlist_opts["cookiefile"] = str(COOKIES_FILE)
        elif _firefox_cookie_source_available("firefox"):
            # FIX: проверяем наличие Firefox-профиля, а не только бинаря.
            # shutil.which("firefox") находит exe, но без профиля yt-dlp
            # падает с "could not find firefox cookies database".
            playlist_opts["cookiesfrombrowser"] = ("firefox",)
        # FIX: передаём proxy в yt-dlp Python API — без этого в no-TUN режиме
        # playlist metadata fetch идёт напрямую и получает ConnectionResetError.
        _proxy = _proxy_for_ytdlp()
        if _proxy:
            playlist_opts["proxy"] = _proxy
        loop = asyncio.get_running_loop()
        info = await loop.run_in_executor(
            None,
            lambda: yt_dlp.YoutubeDL(playlist_opts).extract_info(url, download=False),
        )
        if not info:
            await safe_edit_text(status_msg, "❌ Не удалось загрузить плейлист.")
            return
        title   = info.get("title", "Плейлист")
        entries = info.get("entries", [])[:MAX_PLAYLIST_SIZE]
        total   = len(entries)
        if not entries:
            await safe_edit_text(status_msg, "❌ Плейлист пуст.")
            return
        ai_status = "✅" if GEMINI_CLIENTS else "❌"
        await status_msg.edit_text(
            f"📋 {title}\n🎬 Записей: {total}\n🧠 AI: {ai_status}\n🎵 128 kbps\n⏳ Начинаю..."
        )
        await asyncio.sleep(2)
        success = fail = 0
        is_vip = user_id in WHITELIST_IDS

        for i, entry in enumerate(entries, 1):
            media_url = _build_media_url(entry)
            if not media_url:
                fail += 1
                failed_entries.append(f"[{i}] (нет URL у entry)")
                continue

            # PART5: атомарная reservation перед каждым видео. Делаем её ПОСЛЕ
            # проверки URL, чтобы битый playlist entry не съедал дневной лимит.
            if user_id and not is_vip:
                allowed, reason = await areserve_rate_limit(user_id)
                if not allowed:
                    await status_msg.edit_text(
                        f"📋 {title}\n"
                        f"⏸ Лимит исчерпан после {i-1}/{total}\n"
                        f"✅ {success}  ❌ {fail}\n\n"
                        f"{reason}"
                    )
                    break

            prefix = f"[{i}/{total}] "
            try:
                await safe_edit_text(status_msg,
                    f"📋 {title}\n🔄 {i}/{total} | ✅ {success} | ❌ {fail}"
                )
            except Exception:
                pass

            # PART5: тот же per-video lock, что и в single-video handler.
            # Иначе два одинаковых entry в плейлисте или два параллельных плейлиста
            # могли одновременно пройти cache miss и начать двойную обработку.
            _vid_lock_key = str(entry.get("id") or media_url)
            _vlock = _get_video_lock(_vid_lock_key)
            if _vlock.locked():
                logger.info("Playlist video %s: duplicate request — waiting for lock", _vid_lock_key)
            try:
                async with _vlock:
                    # AUDIT M3: silent_errors=True — process_single_video не шлёт reply_text
                    ok = await process_single_video(
                        media_url, update, status_msg, prefix,
                        context=context, silent_errors=True,
                    )
            finally:
                # async with освобождает lock, release чистит registry даже при исключении.
                _release_video_lock(_vid_lock_key, _vlock)

            if ok:
                success += 1
                # rate-limit уже зарезервирован до обработки через areserve_rate_limit()
            else:
                fail += 1
                failed_entries.append(f"[{i}] {media_url}")

            if i < total:
                await asyncio.sleep(PLAYLIST_DELAY_SEC)

        # ── Финальное сообщение с деталями ──
        final = f"🏁 Плейлист обработан!\n📋 {title}\n✅ {success} | ❌ {fail} | 📊 {total}"
        if failed_entries:
            # AUDIT M3: показываем первые N упавших ссылок
            preview = "\n".join(failed_entries[:5])
            more = (
                f"\n... и ещё {len(failed_entries) - 5}"
                if len(failed_entries) > 5 else ""
            )
            final += f"\n\nНе удалось:\n{preview}{more}"
        try:
            await status_msg.edit_text(final)
        except Exception:
            # Сообщение могло устареть — отправим новым
            await update.message.reply_text(final)
    except Exception as e:
        logger.error(f"Ошибка плейлиста: {_mask(str(e))}")
        try:
            await status_msg.edit_text(f"❌ Ошибка: {_mask(str(e))[:300]}")
        except Exception:
            await update.message.reply_text(f"❌ Ошибка: {_mask(str(e))[:300]}")
