import os
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bot.py — совместимый launcher для MP3Bot.

Реальная точка входа после рефакторинга — `bot_new.py`. Этот файл оставлен
сознательно как тонкая прослойка для совместимости со старыми скриптами
(.bat, systemd, привычка пользователей запускать `python bot.py`).

Что делает этот файл:
    1. Печатает короткий баннер, чтобы было видно, какая версия запускается.
    2. Импортирует и вызывает `main()` из `bot_new.py`, где уже выполнены:
         • проверка версии Python;
         • ранняя загрузка `.env` (load_dotenv) ДО тяжёлых импортов;
         • ранняя валидация BOT_TOKEN / GEMINI_API_KEY с понятной ошибкой
           вместо длинного traceback;
         • запуск всей цепочки: core → services → handlers → pipelines.

Если хочется полностью убрать прослойку, можно запускать напрямую:
        python bot_new.py        # Linux / macOS
        py -3.13 bot_new.py      # Windows

История исправлений (этот rev):
    • FIX VK-поиск: ранее обработка items была ошибочно завёрнута в
      `if len(items) == 0:`, поэтому видео, найденное в VK (items=1+),
      молча превращалось в None. Видно по логам:
            VK ответ: status=200
            VK items: 1
            ...
            Альт-ссылки: rutube=..., vk=None
      Теперь логика прямая: 0 → диагностика и None, ≥1 → _best_match.
      См. services/search.py.
    • FIX конспекты — `NameError: name '_SCRIPTURE_REF_RE' is not defined`:
      при рефакторинге попытались вынести регексы Scripture-refs на
      module-level, но определения были утеряны. Из-за этого падали
      Synopsis v2, StudyAnalysis и ReflectionApplication (все шли в
      fallback, и в Telegraph попадали урезанные страницы).
      Восстановлены _SCRIPTURE_BOOK_RE / _SCRIPTURE_REF_RE /
      _SCRIPTURE_CHAIN_RE на уровне модуля.
      См. converters/md_telegraph.py.
"""

from __future__ import annotations



_BANNER = (
    "──────────────────────────────────────────────────────────────\n"
    " MP3Bot · launcher (bot.py → bot_new.py)\n"
    " Запускается актуальная точка входа bot_new.py\n"
    " Если запускали по привычке — это нормально, всё работает.\n"
    "──────────────────────────────────────────────────────────────"
)


def _print_banner() -> None:
    try:
        print(_BANNER, flush=True)
    except Exception:
        # На случай экзотических stdout (например, без поддержки UTF-8 в cp1251)
        try:
            print(_BANNER.encode("ascii", "replace").decode("ascii"))
        except Exception:
            pass


def main() -> None:
    """Тонкая обёртка: делегирует запуск в bot_new.main()."""
    _print_banner()
    # Импорт здесь, а не на уровне модуля: если кто-то делает
    # `from bot import main` — баннер не должен печататься повторно.
    from bot_new import main as _entry
    _entry()


if __name__ == "__main__":
    main()
