from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if new in text:
        return
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one integration anchor, found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8", newline="\n")


def append_once(path: Path, marker: str, block: str) -> None:
    text = path.read_text(encoding="utf-8")
    if marker in text:
        return
    path.write_text(text.rstrip() + "\n\n" + block.strip() + "\n", encoding="utf-8", newline="\n")


def patch_main() -> None:
    path = ROOT / "main.py"
    replace_once(
        path,
        "from handlers.mode_command import mode_command, handle_mode_callback\n",
        "from handlers.mode_command import mode_command, handle_mode_callback\n"
        "from handlers.dub_production import (\n"
        "    dub_command, handle_dub_callback,\n"
        "    handle_dub_translation_document, handle_dub_translation_text,\n"
        ")\n",
    )
    replace_once(
        path,
        '    app.add_handler(CommandHandler("mode",       mode_command, filters=_MSG_ONLY))\n',
        '    app.add_handler(CommandHandler("mode",       mode_command, filters=_MSG_ONLY))\n'
        '    app.add_handler(CommandHandler("dub",        dub_command, filters=_MSG_ONLY))\n',
    )
    replace_once(
        path,
        "    app.add_handler(MessageHandler(\n"
        "        filters.TEXT & ~filters.COMMAND & filters.UpdateType.MESSAGE, handle_message))\n"
        "    app.add_handler(CallbackQueryHandler(handle_mode_callback, pattern=\"^set_mode:\"))\n"
        "    app.add_handler(CallbackQueryHandler(handle_callback))\n",
        "    # Approved-translation production replies run before the legacy URL handler.\n"
        "    # Unrelated messages pass through to group 1 unchanged.\n"
        "    app.add_handler(MessageHandler(\n"
        "        filters.TEXT & ~filters.COMMAND & filters.UpdateType.MESSAGE,\n"
        "        handle_dub_translation_text,\n"
        "    ), group=0)\n"
        "    app.add_handler(MessageHandler(\n"
        "        filters.Document.ALL & filters.UpdateType.MESSAGE,\n"
        "        handle_dub_translation_document,\n"
        "    ), group=0)\n"
        "    app.add_handler(MessageHandler(\n"
        "        filters.TEXT & ~filters.COMMAND & filters.UpdateType.MESSAGE, handle_message),\n"
        "        group=1,\n"
        "    )\n"
        "    app.add_handler(CallbackQueryHandler(handle_mode_callback, pattern=\"^set_mode:\"))\n"
        "    app.add_handler(CallbackQueryHandler(handle_dub_callback, pattern=\"^dub:\"))\n"
        "    app.add_handler(CallbackQueryHandler(handle_callback))\n",
    )
    replace_once(
        path,
        '                    BotCommand("settings",   "⚙️ Настройки бота"),\n',
        '                    BotCommand("settings",   "⚙️ Настройки бота"),\n'
        '                    BotCommand("dub",        "🎬 Дубляж из готового перевода"),\n',
    )


def patch_commands() -> None:
    path = ROOT / "handlers" / "commands.py"
    replace_once(
        path,
        '                "/promptrecommend [n] — лучший prompt variant"\n',
        '                "/promptrecommend [n] — лучший prompt variant\\n"\n'
        '                "/dub &lt;url&gt; — VoxCPM2 production из утверждённого перевода"\n',
    )
    replace_once(
        path,
        '            f"• Отправьте ссылку на видео или плейлист\\n"\n'
        '            f"• Получите MP3 128kbps + обложка\\n\\n"\n',
        '            f"• Отправьте ссылку на видео или плейлист\\n"\n'
        '            f"• Получите MP3 128kbps + обложка\\n"\n'
        '            f"• <code>/dub URL</code> — production-дубляж из уже утверждённого перевода\\n\\n"\n',
    )
    replace_once(
        path,
        '        f"/mode — 🌐 Режим: RUS / ENG Full / ENG Quick\\n"\n',
        '        f"/mode — 🌐 Режим: RUS / ENG Full / ENG Quick\\n"\n'
        '        f"/dub &lt;URL&gt; — 🎬 VoxCPM2-дубляж из готового перевода\\n"\n',
    )
    replace_once(
        path,
        '        f"🔑 Для стабильных «Живых голосов» нужен VOT_API_TOKEN (или YANDEX_OAUTH_TOKEN) в .env"\n',
        '        f"🔑 Для стабильных «Живых голосов» нужен VOT_API_TOKEN (или YANDEX_OAUTH_TOKEN) в .env\\n\\n"\n'
        '        f"🎬 <b>Production /dub:</b> пришлите ссылку или ответьте командой на видео, "\n'
        '        f"затем ответом отправьте уже проверенный TXT, MD, DOCX или текст. "\n'
        '        f"Бот не переводит и не переписывает утверждённую редакцию."\n',
    )


def patch_requirements() -> None:
    path = ROOT / "requirements.txt"
    replace_once(
        path,
        "beautifulsoup4>=4.12.0\n",
        "beautifulsoup4>=4.12.0\n\n"
        "# ── Импорт утверждённых переводов DOCX ───────────────────────────────────\n"
        "python-docx>=1.1.0,<2.0.0\n",
    )


def patch_readme() -> None:
    path = ROOT / "README.md"
    block = """
## Production-дубляж из утверждённого перевода

Команда `/dub` создаёт долговечный VoxCPM2-проект из исходной ссылки или видеофайла и уже проверенного русского перевода.

```text
/dub https://youtube.com/...
```

После создания проекта ответьте на карточку проекта окончательным переводом как обычным текстом либо файлом `.txt`, `.md` или `.docx`.

Контракт режима:

- перевод считается редакционно утверждённым до загрузки в бота;
- бот не переводит, не сокращает и не переписывает его;
- утверждённая ревизия хранится с SHA-256 и снимается с production-готовности при любом изменении;
- VoxCPM2 работает только на CPU, скрытый TTS fallback запрещён;
- до 180 секунд используется профиль `shorts_premium` с прожигом субтитров и отдельным SRT;
- длиннее 180 секунд используется `long_premium` без обязательного hardsub, но с отдельным SRT;
- надписи, уже находящиеся в исходном кадре, не переводятся;
- проект хранит атомарный `manifest.json` и неизменяемый журнал `events.jsonl`.

Кнопка «Проверить готовность» выполняет лёгкий preflight без загрузки VoxCPM2: проверяет источник, длительность, перевод, CPU-окружение, snapshot модели, FFmpeg, production-модули и свободное место.
"""
    append_once(path, "## Production-дубляж из утверждённого перевода", block)


def patch_env() -> None:
    path = ROOT / ".env.example"
    block = r"""
# ── VoxCPM2 production из утверждённого перевода ─────────────────
# Постоянное хранилище проектов: source/editorial/references/segments/
# synthesis/masters/outputs/reports + manifest.json + events.jsonl.
# DUB_PROJECTS_DIR=C:\AI-Archive\MP3Bot-Dub-Projects
# Максимальный исходный Telegram-видеофайл и файл перевода.
# DUB_MAX_SOURCE_MB=2000
# DUB_MAX_TRANSLATION_MB=10
# DUB_TRANSLATION_MAX_CHARS=1000000
# Таймаут получения метаданных URL через yt-dlp.
# DUB_URL_PROBE_TIMEOUT_SEC=180
# Изолированное CPU-окружение и локальный архив VoxCPM2.
# VOXCPM2_CPU_PYTHON=C:\AI-Archive\VoxCPM2-CPU-TEST\.venv\Scripts\python.exe
# VOXCPM2_ARCHIVE_ROOT=C:\AI-Archive\VoxCPM2-paused-RTX3060
"""
    append_once(path, "# ── VoxCPM2 production из утверждённого перевода", block)


def main() -> None:
    patch_main()
    patch_commands()
    patch_requirements()
    patch_readme()
    patch_env()
    print("approved-dub integration applied")


if __name__ == "__main__":
    main()
