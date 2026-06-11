#!/usr/bin/env python3
"""
main.py — run_bot_async, run_bot, main().

⚠️  Не запускать напрямую — используй bot_new.py.
    bot_new.py выполняет load_dotenv() и валидацию BOT_TOKEN до импорта
    тяжёлых зависимостей. Прямой запуск main.py это шаг пропускает.
"""
from core.globals import (
    BOT_TOKEN, LOCAL_BOT_API_URL, flask_app, DB_PATH,
    GEMINI_CLIENTS, DAILY_LIMIT, COOLDOWN_SECONDS,
)
from core.database import (
    db_init, asettings_get,
    GEMINI_MODEL, WHITELIST_IDS, ADMIN_IDS,
)
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters,
)
from services.shorts_video import (
    HAS_FASTER_WHISPER,
    get_subtitles_mode_settings,
    _get_whisper_model,
)
from handlers.commands import (
    start, help_command, handle_message, reset_cache_command, pdf_command,
    stop_command, metrics_command, archive_command, lastpages_command,
    search_archive_command, author_archive_command, scripture_archive_command,
    repairpage_command, repairrecent_command, segments_command, cutseg_command,
    prompthealth_command, codehealth_command, archivequality_command, archivequalityfile_command, qualityrecords_command, promptrecommend_command, comparevariants_command, archivefile_command, segmentfile_command,
)
from handlers.callbacks import handle_callback, settings_command
from handlers.commands import status_command  # round 32
from handlers.mode_command import mode_command, handle_mode_callback

import asyncio
import logging
import os
import signal
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

async def run_bot_async():
    if not BOT_TOKEN:
        logger.error("❌ BOT_TOKEN не найден!")
        return

    # AUDIT C5: чистим словарь per-video locks. После краша run_bot_async и
    # создания нового event loop старые Lock'и привязаны к мёртвому loop,
    # что в Python 3.10+ даёт RuntimeError при первом параллельном запросе.
    from core.globals import _video_processing_locks, _video_locks_mutex
    with _video_locks_mutex:
        _stale = len(_video_processing_locks)
        _video_processing_locks.clear()
    if _stale:
        logger.info(f"🧹 Очищено per-video locks от предыдущего запуска: {_stale}")

    logger.info("🚀 Бот запускается...")
    # AUDIT 2026-06-10: startup-диагностика внешних инструментов.
    # Без ffprobe МОЛЧА деградируют: метаданные видео (превью/стриминг),
    # LUFS-выравнивание pro-микса, тех-проверка перевода, длительность аудио.
    import shutil as _sh
    try:
        from services.ffmpeg import _supported_js_runtimes as _yt_js_runtimes
        _yt_js_ok = bool(_yt_js_runtimes())
    except Exception:
        _yt_js_ok = False
    _vot_helper_ok = bool(_sh.which("node") and (Path("vot_helper") / "vot_live.mjs").exists())
    _tools = {
        "ffmpeg": _sh.which("ffmpeg"),
        "ffprobe": _sh.which("ffprobe"),
        "yt-dlp (модуль)": True,  # ставится pip-ом вместе с ботом
        "yt-dlp JS runtime (Deno>=2.3/Node>=22)": _yt_js_ok,
        "VOT helper (@vot.js/node)": _vot_helper_ok,
        "vot-cli-live fallback": bool(_sh.which("vot-cli-live") or _sh.which("vot-cli-live.cmd")),
    }
    for _tname, _tpath in _tools.items():
        if _tpath:
            logger.info(f"🔧 {_tname}: ✅")
        else:
            logger.warning(f"🔧 {_tname}: ❌ ОТСУТСТВУЕТ — часть функций молча деградирует")
    # ROUND 39: живые голоса Яндекса требуют OAuth-токен (SESSION_REQUIRED
    # с VOT 1.10). Без него live-перевод работает только из серверного кэша.
    _vot_oauth_ok = bool((os.getenv("VOT_API_TOKEN", "") or os.getenv("YANDEX_OAUTH_TOKEN", "")).strip())
    if _vot_oauth_ok:
        logger.info("🔧 VOT_API_TOKEN/YANDEX_OAUTH_TOKEN: ✅ (живые голоса авторизованы)")
    else:
        logger.warning(
            "🔧 VOT_API_TOKEN/YANDEX_OAUTH_TOKEN: ❌ НЕ ЗАДАН — «Живые голоса» будут работать только "
            "для уже переведённых кем-то роликов. Инструкция в .env.example"
        )
    try:
        from services.livedub_mix import get_mix_params as _ld_mix_params
        from core.database import current_livedub_file_id_cache_fingerprint as _ld_fingerprint
        _ldp = _ld_mix_params()
        _vot_pad = os.getenv("LIVEDUB_VOT_DURATION_PAD_SEC", "1").strip() or "1"
        logger.info(
            "🎬 LiveDub mix: delay=%dms base_tail=%dms freeze<=%ss vot_pad=%ss cache_fp=%s",
            _ldp.get("delay_ms", 0), _ldp.get("tail_pad_ms", 0),
            _ldp.get("tail_freeze_max_sec", 0), _vot_pad, _ld_fingerprint(),
        )
    except Exception as _ld_diag_err:
        logger.info("🎬 LiveDub mix diagnostics unavailable: %s", str(_ld_diag_err)[:120])
    logger.info(f"🧠 AI ({GEMINI_MODEL}): {'✅' if GEMINI_CLIENTS else '❌'} (ключей: {len(GEMINI_CLIENTS)})")

    # AUDIT L6: обновлённые списки моделей по официальной странице
    # https://ai.google.dev/gemini-api/docs/deprecations (на 2026-05-20)
    _KNOWN_LIVE_MODELS = {
        # Gemini 3.5 (GA с 19 мая 2026 — AUDIT-FIX BUG 3)
        "gemini-3.5-flash",
        # Gemini 3
        "gemini-3-flash",
        "gemini-3-flash-preview",
        "gemini-3.1-pro-preview",
        "gemini-3.1-flash-lite",
        "gemini-3.1-flash-lite-preview",
        # Gemini 2.5 (stable до 16 окт 2026, 2.5-flash/pro до 17 июня 2026)
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
        "gemini-2.5-pro",
        "gemini-2.5-pro-preview",
    }
    _DEPRECATED_MODELS = {
        "gemini-2.0-flash",
        "gemini-2.0-flash-001",
        "gemini-2.0-flash-lite",
        "gemini-1.5-pro",
        "gemini-1.5-flash",
        "gemini-1.5-flash-001",
        "gemini-1.0-pro",
        "gemini-pro",
        "gemini-pro-vision",
    }
    if GEMINI_MODEL in _DEPRECATED_MODELS:
        logger.warning(
            "⚠️  GEMINI_MODEL='%s' — устарела и скоро будет отключена. "
            "Рекомендуется GEMINI_MODEL='gemini-2.5-flash' (стабильная) или "
            "'gemini-3-flash-preview' (новая, free tier).",
            GEMINI_MODEL,
        )
    elif GEMINI_MODEL == "gemini-2.5-pro":
        logger.warning(
            "⚠️  GEMINI_MODEL='gemini-2.5-pro' — с 1 апреля 2026 Pro-модели "
            "требуют платного биллинга (free tier больше не работает). "
            "Если ключи free tier — все запросы получат 429/quota error, "
            "и бот будет выдавать только базовую информацию без анализа. "
            "Рекомендуется GEMINI_MODEL='gemini-2.5-flash' в .env"
        )
    elif GEMINI_MODEL not in _KNOWN_LIVE_MODELS:
        logger.warning(
            "⚠️  GEMINI_MODEL='%s' — модель не входит в список проверенных живых моделей. "
            "Если бот падает при первом запросе — проверьте правильность имени модели.",
            GEMINI_MODEL,
        )
    logger.info(f"🛡  Whitelist: {len(WHITELIST_IDS)} VIP-пользователей")
    logger.info(f"📵 Лимит: {DAILY_LIMIT} видео/день | {COOLDOWN_SECONDS}с между запросами")
    _subtitles_enabled = await asettings_get("shorts_subtitles")
    if _subtitles_enabled and not HAS_FASTER_WHISPER:
        logger.warning("⚠️  Субтитры включены, но faster-whisper НЕ установлен! Установите: pip install faster-whisper")
    elif HAS_FASTER_WHISPER:
        _sub_cfg = get_subtitles_mode_settings()
        if _subtitles_enabled:
            _sub_mode = (
                f"модель={_sub_cfg['model_name']} | "
                f"karaoke={'вкл' if _sub_cfg['karaoke'] else 'выкл'} | "
                f"режим={'лёгкий' if _sub_cfg['light'] else 'полный'}"
            )
            # AUDIT: проверяем что есть качественный шрифт для субтитров
            try:
                from services.shorts_video import _pick_subtitle_font
                _font = _pick_subtitle_font()
                if _font in ("Arial", "Arial Bold"):
                    logger.warning(
                        "⚠️  Шрифт субтитров: %s (fallback). "
                        "Для красивых субтитров установите Montserrat ExtraBold: "
                        "https://fonts.google.com/specimen/Montserrat → Download family → "
                        "распакуйте Montserrat-ExtraBold.ttf в C:/Windows/Fonts/", _font
                    )
                else:
                    logger.info(f"🅰️  Шрифт субтитров: {_font}")
            except Exception as _fe:
                logger.debug(f"font check: {_fe}")
            logger.info(f"💬 Субтитры Shorts: ✅ включены ({_sub_mode})")
            # Preload запустим в фоне после старта бота — не блокируем polling
            async def _preload_whisper_bg():
                try:
                    loop = asyncio.get_running_loop()
                    await loop.run_in_executor(None, lambda: _get_whisper_model(_sub_cfg["model_name"]))
                    logger.info(f"🎤 Whisper preloaded: {_sub_cfg['model_name']}")
                except Exception as _e:
                    logger.warning(f"⚠️  Whisper preload не удался: {_e}")
            asyncio.create_task(_preload_whisper_bg())
        else:
            logger.info("💬 Субтитры Shorts: ⬜ выключены")
    else:
        logger.info("💬 Субтитры Shorts: ❌ (faster-whisper не установлен)")

    from telegram.request import HTTPXRequest

    # Кастомный request с увеличенными таймаутами — решает проблему
    # обрывов соединения в httpx 0.28+ (агрессивное закрытие idle-соединений)
    # FIX: если в системе задан HTTP(S)_PROXY, httpx гонит через него даже
    # запросы к 127.0.0.1 — прокси не достучится до localhost и всё виснет
    # по таймауту (TimedOut на getMe). Исключаем localhost из прокси.
    # ВАЖНО: делать ДО создания HTTPXRequest — httpx читает env при создании клиента.
    if LOCAL_BOT_API_URL:
        _no_proxy = os.environ.get("NO_PROXY", "")
        if "127.0.0.1" not in _no_proxy:
            os.environ["NO_PROXY"] = f"{_no_proxy},127.0.0.1,localhost".strip(",")
            os.environ["no_proxy"] = os.environ["NO_PROXY"]
            logger.info(f"🚫 NO_PROXY дополнен: {os.environ['NO_PROXY']}")

    _telegram_proxy_url = os.getenv("TELEGRAM_PROXY_URL", "").strip()
    _request_kwargs = dict(
        connection_pool_size=32,
        read_timeout=120.0,
        write_timeout=120.0,
        connect_timeout=120.0,
        pool_timeout=120.0,
    )
    if _telegram_proxy_url and not LOCAL_BOT_API_URL:
        # Cloud Bot API mode: PTB/httpx ходит к api.telegram.org через proxy.
        # Для socks5/socks5h нужен extra python-telegram-bot[socks].
        _request_kwargs["proxy"] = _telegram_proxy_url
        logger.info("🌐 Telegram Bot API: использую TELEGRAM_PROXY_URL для Bot API/getUpdates")
    elif _telegram_proxy_url and LOCAL_BOT_API_URL:
        logger.info(
            "🌐 TELEGRAM_PROXY_URL задан, но LOCAL_BOT_API_URL указывает на localhost — "
            "Python-бот ходит в локальный сервер напрямую; proxy для самого сервера задавайте LOCAL_BOT_API_PROXY_URL"
        )
    t_request = HTTPXRequest(**_request_kwargs)
    get_updates_request = HTTPXRequest(**_request_kwargs)

    # FIX 2026-06-10: по умолчанию PTB обрабатывает апдейты ПОСЛЕДОВАТЕЛЬНО —
    # во время 10-20-минутной обработки видео бот не отвечал ни на /stop,
    # ни на /mode, ни на /help (наблюдалось живьём при первом тесте /mode).
    # concurrent_updates(8): команды отвечают мгновенно; гонки закрыты ранее:
    # video-lock по media_id (_get_video_lock), atomic UPSERT в rate_limit,
    # WAL+busy_timeout в SQLite.
    builder = (Application.builder().token(BOT_TOKEN)
               .request(t_request).get_updates_request(get_updates_request).concurrent_updates(8))
    if LOCAL_BOT_API_URL:
        # FIX 2026-06-11 (live log 02:03): сервер не запущен -> ConnectError
        # и бесконечный restart-цикл с полным трейсбеком каждые 5с (+ Whisper
        # перегружался каждый раз). Pre-flight TCP-проверка: ждём сервер с
        # понятным сообщением вместо стены трейсбеков.
        import socket
        import subprocess as _sp
        from urllib.parse import urlparse
        _u = urlparse(LOCAL_BOT_API_URL)
        _host, _port = _u.hostname or "127.0.0.1", _u.port or 80

        def _try_autostart_botapi() -> bool:
            """FIX 2026-06-11: бот сам запускает telegram-bot-api.exe.

            Start Bot.bat в репозитории не содержит автостарт сервера
            (секреты нельзя коммитить в bat) — теперь ключи берутся из
            .env: TELEGRAM_API_ID / TELEGRAM_API_HASH. Сервер стартует
            detached: переживает рестарты бота. LOCAL_BOT_API_AUTOSTART=0
            отключает."""
            if os.getenv("LOCAL_BOT_API_AUTOSTART", "1").strip().lower() in {"0", "false", "no", "off"}:
                return False
            _api_id = os.getenv("TELEGRAM_API_ID", "").strip()
            _api_hash = os.getenv("TELEGRAM_API_HASH", "").strip()
            from pathlib import Path as _P
            _exe = os.getenv("LOCAL_BOT_API_EXE",
                             r"C:\Program Files\TelegramBotAPI\telegram-bot-api.exe")
            if not (_api_id and _api_hash):
                logger.warning(
                    "⚠️ Автостарт Bot API: добавьте TELEGRAM_API_ID и TELEGRAM_API_HASH "
                    "в .env (my.telegram.org) — тогда бот будет поднимать сервер сам."
                )
                return False
            if not _P(_exe).exists():
                logger.warning("⚠️ Автостарт Bot API: exe не найден: %s (LOCAL_BOT_API_EXE)", _exe)
                return False
            _data = os.getenv("LOCAL_BOT_API_DATA_DIR",
                              r"C:\ProgramData\TelegramBotAPI\data")
            try:
                _P(_data).mkdir(parents=True, exist_ok=True)
            except OSError:
                pass
            # FIX 2026-06-11b: лог сервера в файл (был DEVNULL — при падении
            # сервера мы не видели ПРИЧИНУ: битые ключи, занятый порт, права).
            _srv_log_path = _P(_data).parent / "botapi-server.log"

            def _tdlib_proxy_args() -> list[str]:
                """Proxy для исходящих соединений telegram-bot-api.exe → Telegram DC.

                Нужен, когда VPN работает не в TUN-режиме, а только как локальный
                HTTP/SOCKS proxy. TELEGRAM_PROXY_URL влияет на Python/PTB, но НЕ
                на отдельный процесс telegram-bot-api.exe.
                """
                from urllib.parse import urlparse, unquote
                proxy_url = os.getenv("LOCAL_BOT_API_PROXY_URL", "").strip()
                ptype = os.getenv("LOCAL_BOT_API_TDLIB_PROXY_TYPE", "").strip().lower()
                server = os.getenv("LOCAL_BOT_API_PROXY_SERVER", "").strip()
                port = os.getenv("LOCAL_BOT_API_PROXY_PORT", "").strip()
                login = os.getenv("LOCAL_BOT_API_PROXY_LOGIN", "").strip()
                password = os.getenv("LOCAL_BOT_API_PROXY_PASSWORD", "").strip()
                secret = os.getenv("LOCAL_BOT_API_PROXY_SECRET", "").strip()
                if proxy_url:
                    u = urlparse(proxy_url)
                    ptype = (u.scheme or ptype).lower().replace("socks5h", "socks5")
                    server = u.hostname or server
                    port = str(u.port or port)
                    login = unquote(u.username or login or "")
                    password = unquote(u.password or password or "")
                if not (ptype and server and port):
                    return []
                if ptype not in {"socks5", "http", "mtproto"}:
                    logger.warning("⚠️ LOCAL_BOT_API proxy type %r не поддержан; нужен socks5/http/mtproto", ptype)
                    return []
                out = [f"--proxy-server={server}", f"--proxy-port={port}", f"--tdlib-proxy-type={ptype}"]
                if login:
                    out.append(f"--proxy-login={login}")
                if password:
                    out.append(f"--proxy-password={password}")
                if secret:
                    out.append(f"--proxy-secret={secret}")
                logger.info("🌐 telegram-bot-api.exe: TDLib proxy включён (%s://%s:%s)", ptype, server, port)
                return out

            _botapi_proxy_args = _tdlib_proxy_args()
            cmd = [_exe, f"--api-id={_api_id}", f"--api-hash={_api_hash}",
                   "--local", f"--http-port={_port}", f"--dir={_data}",
                   f"--log={_srv_log_path}", "--verbosity=2", *_botapi_proxy_args]
            kwargs: dict = {"stdout": _sp.DEVNULL, "stderr": _sp.DEVNULL}
            if os.name == "nt":
                # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
                kwargs["creationflags"] = 0x8 | 0x200 | 0x08000000
            else:
                kwargs["start_new_session"] = True
            def _spawn(_cmd, _log_path):
                _pr = _sp.Popen(_cmd, **kwargs)
                import time as _t
                _t.sleep(2)
                if _pr.poll() is None:
                    return True, ""
                _tail = ""
                try:
                    _tail = _log_path.read_text(encoding="utf-8", errors="replace")[-600:]
                except OSError:
                    pass
                return False, _tail

            try:
                logger.info("🚀 Автостарт: запускаю telegram-bot-api.exe (порт %s, лог: %s)...",
                            _port, _srv_log_path)
                ok, _tail = _spawn(cmd, _srv_log_path)
                if ok:
                    return True
                # FIX 2026-06-11c (live log 02:28): 'Access is denied' на
                # binlog — data-dir в ProgramData создан АДМИНОМ (PowerShell
                # run-as-admin / планировщик), а бот запускает сервер от
                # обычного юзера. Fallback: user-writable %LOCALAPPDATA%.
                # Сервер заново авторизует бота на чистом data-dir — это
                # штатно (логин по токену при первом запросе).
                if "Access is denied" in _tail or "can't be opened" in _tail:
                    _user_data = _P(os.environ.get("LOCALAPPDATA",
                                    str(_P.home() / "AppData" / "Local"))) / "TelegramBotAPI" / "data"
                    try:
                        _user_data.mkdir(parents=True, exist_ok=True)
                    except OSError:
                        pass
                    _user_log = _user_data.parent / "botapi-server.log"
                    logger.warning(
                        "⚠️ Нет прав на %s (создан от админа). Перезапускаю сервер с "
                        "пользовательской папкой: %s\n"
                        "   Постоянное решение (один раз, от админа):\n"
                        "   PowerShell: icacls \"%s\" /grant \"${env:USERNAME}:(OI)(CI)F\" /T\n"
                        "   cmd.exe:    icacls \"%s\" /grant %%USERNAME%%:(OI)(CI)F /T",
                        _data, _user_data, _data, _data,
                    )
                    cmd2 = [_exe, f"--api-id={_api_id}", f"--api-hash={_api_hash}",
                            "--local", f"--http-port={_port}", f"--dir={_user_data}",
                            f"--log={_user_log}", "--verbosity=2", *_botapi_proxy_args]
                    ok2, _tail2 = _spawn(cmd2, _user_log)
                    if ok2:
                        # FIX round 37: БЕЗУСЛОВНАЯ перезапись (was setdefault).
                        # Если юзер прописал ProgramData-путь в .env (рекомендация
                        # round 9), setdefault его не менял -> cleanup чистил
                        # недоступную папку, а реальная (LOCALAPPDATA) росла
                        # бесконечно — утечка диска в новом месте.
                        os.environ["LOCAL_BOT_API_DATA_DIR"] = str(_user_data)
                        logger.info("🧹 Чистка диска переключена на %s", _user_data)
                        return True
                    _tail = _tail2 or _tail
                logger.error(
                    "❌ telegram-bot-api.exe упал сразу. Хвост лога сервера:\n%s",
                    _tail or "(лог пуст — проверьте права на папку)",
                )
                return False
            except Exception as _ase:
                logger.warning("⚠️ Автостарт Bot API не удался: %s", _ase)
                return False

        _wait_logged = False
        _autostart_tried = False
        for _pf_attempt in range(60):  # до ~5 минут ожидания
            try:
                with socket.create_connection((_host, _port), timeout=2):
                    break
            except OSError:
                if not _autostart_tried:
                    _autostart_tried = True
                    if _try_autostart_botapi():
                        # Первый запуск сервера: создание binlog + коннект к
                        # Telegram DC занимает 5-20с (дольше через VPN/TUN)
                        for _grace in range(10):
                            await asyncio.sleep(2)
                            try:
                                with socket.create_connection((_host, _port), timeout=2):
                                    break
                            except OSError:
                                continue
                        continue
                if not _wait_logged:
                    logger.error(
                        "❌ Локальный Bot API сервер НЕ ЗАПУЩЕН (%s:%s не отвечает).\n"
                        "   Запустите telegram-bot-api.exe (Start Bot.bat делает это сам) или\n"
                        "   проверьте: tasklist | findstr telegram-bot-api\n"
                        "   Жду появления сервера (проверка каждые 5с, до 5 минут)...",
                        _host, _port,
                    )
                    _wait_logged = True
                await asyncio.sleep(5)
        else:
            raise RuntimeError(
                f"Локальный Bot API сервер {_host}:{_port} не поднялся за 5 минут. "
                "Запустите его и перезапустите бота."
            )
        if _wait_logged:
            logger.info("✅ Локальный Bot API сервер появился — продолжаю запуск")
        logger.info(f"🌐 Использую локальный Telegram Bot API: {LOCAL_BOT_API_URL}")
        builder.base_url(f"{LOCAL_BOT_API_URL}/bot")
        builder.base_file_url(f"{LOCAL_BOT_API_URL}/file/bot")
        builder.local_mode(True)

    app = builder.build()
    # FIX 2026-06-10: edited-команды (юзер отредактировал «/modе» → «/mode»)
    # проходят CommandHandler через effective_message, но хендлеры читают
    # update.message (None для edited) → AttributeError. Только новые сообщения.
    _MSG_ONLY = filters.UpdateType.MESSAGE
    app.add_handler(CommandHandler("start",      start, filters=_MSG_ONLY))
    app.add_handler(CommandHandler("help",       help_command, filters=_MSG_ONLY))
    app.add_handler(CommandHandler("resetcache", reset_cache_command, filters=_MSG_ONLY))
    app.add_handler(CommandHandler("settings",   settings_command, filters=_MSG_ONLY))
    app.add_handler(CommandHandler("stop",       stop_command, filters=_MSG_ONLY))
    app.add_handler(CommandHandler("pdf",        pdf_command, filters=_MSG_ONLY))
    app.add_handler(CommandHandler("metrics",    metrics_command, filters=_MSG_ONLY))
    app.add_handler(CommandHandler("status",     status_command, filters=_MSG_ONLY))
    app.add_handler(CommandHandler("prompthealth", prompthealth_command, filters=_MSG_ONLY))
    app.add_handler(CommandHandler("codehealth", codehealth_command, filters=_MSG_ONLY))
    app.add_handler(CommandHandler("archivequality", archivequality_command, filters=_MSG_ONLY))
    app.add_handler(CommandHandler("archivequalityfile", archivequalityfile_command, filters=_MSG_ONLY))
    app.add_handler(CommandHandler("qualityrecords", qualityrecords_command, filters=_MSG_ONLY))
    app.add_handler(CommandHandler("promptrecommend", promptrecommend_command, filters=_MSG_ONLY))
    app.add_handler(CommandHandler("comparevariants", comparevariants_command, filters=_MSG_ONLY))
    app.add_handler(CommandHandler("archive",    archive_command, filters=_MSG_ONLY))
    app.add_handler(CommandHandler("archivefile", archivefile_command, filters=_MSG_ONLY))
    app.add_handler(CommandHandler("segmentfile", segmentfile_command, filters=_MSG_ONLY))
    app.add_handler(CommandHandler("lastpages",  lastpages_command, filters=_MSG_ONLY))
    app.add_handler(CommandHandler("mode",       mode_command, filters=_MSG_ONLY))
    app.add_handler(CommandHandler("search",     search_archive_command, filters=_MSG_ONLY))
    app.add_handler(CommandHandler("author",     author_archive_command, filters=_MSG_ONLY))
    app.add_handler(CommandHandler("scripture",  scripture_archive_command, filters=_MSG_ONLY))
    app.add_handler(CommandHandler("repairpage", repairpage_command, filters=_MSG_ONLY))
    app.add_handler(CommandHandler("repairrecent", repairrecent_command, filters=_MSG_ONLY))
    app.add_handler(CommandHandler("segments",   segments_command, filters=_MSG_ONLY))
    app.add_handler(CommandHandler("cutseg",     cutseg_command, filters=_MSG_ONLY))
    # FIX 2026-06-10: filters.TEXT матчит И edited_message (update.message=None
    # -> AttributeError в handle_message). Реагируем только на новые сообщения:
    # редактирование старой ссылки не должно перезапускать обработку видео.
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.UpdateType.MESSAGE, handle_message))
    app.add_handler(CallbackQueryHandler(handle_mode_callback, pattern="^set_mode:"))
    app.add_handler(CallbackQueryHandler(handle_callback))

    # FIX 2026-06-10: глобальный error handler. Без него необработанное
    # исключение в хендлере = тихая строка 'No error handlers are registered'
    # в логе, пользователь остаётся без ответа и без объяснений.
    async def _global_error_handler(update, context):
        err = context.error
        # NetworkError при поллинге — шум, не показываем юзеру
        import telegram.error as _tg_err
        logger.error("Unhandled handler error: %s: %s", type(err).__name__, err,
                     exc_info=err)
        if isinstance(err, (_tg_err.NetworkError, _tg_err.TimedOut)):
            return
        try:
            if update is not None and getattr(update, "effective_chat", None):
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text="⚠️ Внутренняя ошибка при обработке запроса. Подробности в логе бота.",
                )
        except Exception:
            pass

    app.add_error_handler(_global_error_handler)

    # V3-P1: Render/Railway присылают SIGTERM при redeploy/stop.
    # Переводим его в тот же graceful path, что и /stop. На Windows
    # add_signal_handler недоступен — используем signal.signal fallback.
    loop = asyncio.get_running_loop()

    def _request_stop_from_signal(sig_name: str) -> None:
        logger.warning("🛑 Получен %s — graceful shutdown requested", sig_name)
        app.bot_data["stop_requested"] = True

    for _sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(_sig, _request_stop_from_signal, _sig.name)
        except (NotImplementedError, RuntimeError, ValueError):
            try:
                signal.signal(
                    _sig,
                    lambda _signum, _frame, name=_sig.name: _request_stop_from_signal(name),
                )
            except (ValueError, RuntimeError):
                logger.debug("signal handler не установлен для %s", _sig)

    logger.info("✅ Бот запущен!")

    async def _stop_started_application() -> None:
        """Останавливает polling/application перед выходом из async context."""
        try:
            if app.updater and app.updater.running:
                await app.updater.stop()
        except Exception as _e:
            logger.warning("updater.stop during /stop: %s", _e)
        try:
            if app.running:
                await app.stop()
        except Exception as _e:
            logger.warning("app.stop during /stop: %s", _e)

    async with app:
        await app.initialize()
        await app.start()
        await app.updater.start_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True, read_timeout=120, pool_timeout=120)

        # Регистрируем команды — появится кнопка «Menu» слева от поля ввода
        from telegram import BotCommand, BotCommandScopeDefault, BotCommandScopeChat
        default_commands = [
            BotCommand("start", "▶️ Главная"),
            BotCommand("help",  "ℹ️ Справка"),
            BotCommand("mode",  "🌐 Режим: RUS / ENG + живой перевод"),
        ]
        await app.bot.set_my_commands(default_commands, scope=BotCommandScopeDefault())

        # VIP/Admin видят расширенное меню с /resetcache и /settings
        for admin_id in ADMIN_IDS:
            try:
                vip_commands = default_commands + [
                    BotCommand("settings",   "⚙️ Настройки бота"),
                    BotCommand("status",     "🩺 Здоровье бота"),
                    BotCommand("archive",    "📚 Последние страницы"),
                    BotCommand("search",     "🔎 Поиск по архиву"),
                    BotCommand("segments",   "🧩 Сегменты видео"),
                    BotCommand("cutseg",     "🎬 Вырезать сегмент"),
                    BotCommand("repairpage", "🛠 Ремонт Telegraph"),
                    BotCommand("archivefile", "📁 Файл архива"),
                    BotCommand("segmentfile", "🧩 Файл сегментов"),
                    BotCommand("metrics",    "📊 Gemini метрики"),
                    BotCommand("prompthealth", "🧪 Здоровье промптов"),
                    BotCommand("codehealth", "🧰 Regex/code health"),
                    BotCommand("archivequality", "🧪 Качество архива"),
                    BotCommand("archivequalityfile", "📁 Export качества"),
                    BotCommand("qualityrecords", "🧯 Quality records"),
                    BotCommand("promptrecommend", "🧭 Prompt recommend"),
                    BotCommand("comparevariants", "🧪 Сравнить variants"),
                    BotCommand("pdf",        "📄 PDF из кэша"),
                    BotCommand("resetcache", "🗑 Сбросить кэш видео"),
                    BotCommand("stop",       "🛑 Остановить бота"),
                ]
                await app.bot.set_my_commands(
                    vip_commands, scope=BotCommandScopeChat(chat_id=admin_id)
                )
            except Exception:
                pass

        # AUDIT M9: фоновая периодическая чистка временных файлов и БД
        from core.utils import (
            cleanup_nosub_files, cleanup_stale_downloads,
            cleanup_botapi_server_files, cleanup_stale_livedub_dirs,
        )
        from core.database import db_cleanup_old_records, db_backup
        from core.globals import mark_bot_alive

        async def _periodic_maintenance():
            loop = asyncio.get_running_loop()
            while not app.bot_data.get("stop_requested", False):
                mark_bot_alive()
                try:
                    await loop.run_in_executor(None, cleanup_nosub_files)
                    await loop.run_in_executor(None, cleanup_stale_downloads)
                    await loop.run_in_executor(None, db_cleanup_old_records)
                    await loop.run_in_executor(None, db_backup)
                    # AUDIT 2026-06-10: data-dir локального Bot API сервера и
                    # осиротевшие livedub_* в %TEMP% росли бесконечно
                    await loop.run_in_executor(None, cleanup_botapi_server_files)
                    await loop.run_in_executor(None, cleanup_stale_livedub_dirs)
                    # Segment export files (JSON/MD) older than 90 days
                    try:
                        from core.generated_pages import cleanup_old_segment_files
                        await loop.run_in_executor(None, cleanup_old_segment_files)
                    except Exception:
                        pass
                except Exception as _e:
                    logger.warning(f"periodic maintenance: {_e}")
                await asyncio.sleep(3600)

        asyncio.create_task(_periodic_maintenance())

        while True:
            if app.bot_data.get("stop_requested"):
                logger.info("🛑 Stop requested — останавливаем polling/application")
                await _stop_started_application()
                return "stop_requested"
            mark_bot_alive()
            # Спим короткими шагами, чтобы /stop не ждал до 60 секунд.
            for _ in range(60):
                if app.bot_data.get("stop_requested"):
                    logger.info("🛑 Stop requested — останавливаем polling/application")
                    await _stop_started_application()
                    return "stop_requested"
                await asyncio.sleep(1)


def run_bot():
    restart_delay = 5   # базовая задержка между перезапусками
    _net_fail_streak = 0
    while True:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(run_bot_async())
            if result == "stop_requested":
                logger.info("🛑 Бот остановлен по /stop — без автоматического restart")
                break
            _net_fail_streak = 0
        except Exception as e:
            # FIX 2026-06-11: сетевые ошибки (сервер/сеть недоступны) — ОДНА
            # понятная строка вместо 100-строчного трейсбека каждые 5 секунд;
            # экспоненциальный backoff до 60с, чтобы не молотить лог и не
            # перегружать Whisper-прелоад на каждом рестарте.
            _emsg = str(e)
            _is_net = ("ConnectError" in _emsg or "ConnectTimeout" in _emsg
                       or "NetworkError" in type(e).__name__ or "TimedOut" in type(e).__name__)
            if _is_net:
                _net_fail_streak += 1
                logger.error(
                    "🔌 Сеть/Bot API недоступны (%s). Подряд: %d. "
                    "Если это локальный сервер — проверьте telegram-bot-api.exe.",
                    _emsg[:120], _net_fail_streak,
                )
            else:
                _net_fail_streak = 0
                logger.error(f"run_bot завершился с ошибкой: {e}", exc_info=True)
        finally:
            try:
                loop.close()
            except Exception:
                pass
        _delay = min(restart_delay * (2 ** min(_net_fail_streak, 4)), 60) if _net_fail_streak else restart_delay
        logger.warning(f"🔄 Бот перезапускается через {_delay} сек...")
        time.sleep(_delay)


def main():
    # AUDIT: если DISABLE_HEALTH_CHECK=1 (локальный запуск на Windows),
    # запускаем бота в main thread напрямую без Flask.
    # Flask daemon=True → если порт занят, бот убивается мгновенно.
    if os.getenv("DISABLE_HEALTH_CHECK", "").strip().lower() in {"1", "true", "yes", "on"}:
        logger.info("🏠 Локальный режим: Flask отключён (DISABLE_HEALTH_CHECK=1)")
        run_bot()
        return
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    port = int(os.environ.get("PORT", 10000))
    # Используем production-grade WSGI-сервер вместо Flask dev-сервера.
    # waitress: многопоточный, не выдаёт "WARNING: Do not use dev server in production".
    try:
        from waitress import serve
        logger.info(f"🌐 HTTP health-check: waitress на порту {port}")
        serve(flask_app, host="0.0.0.0", port=port, threads=4)
    except ImportError:
        if (os.getenv("ENV") or os.getenv("APP_ENV") or "").strip().lower() in {"prod", "production"}:
            raise RuntimeError("waitress is required in production; install requirements.txt")
        # Fallback на Flask dev-сервер если waitress не установлен.
        # Добавьте waitress в requirements.txt: waitress>=3.0.0,<4.0.0
        logger.warning(
            "⚠️  waitress не установлен — используется Flask dev-сервер. "
            "Установите: pip install waitress"
        )
        flask_app.run(host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
