#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import runpy

os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
"""
bot.py — совместимый launcher для MP3Bot.

Реальная точка входа после рефакторинга — `bot_new.py`. Этот файл оставлен
сознательно как тонкая прослойка для совместимости со старыми скриптами
(.bat, systemd, привычка пользователей запускать `python bot.py`).

Что делает этот файл:
    1. Печатает короткий баннер, чтобы было видно, какая версия запускается.
    2. Выполняет `bot_new.py` как `__main__`, сохраняя тот же bootstrap и lifecycle,
       что и при прямом запуске `python bot_new.py`.

Если хочется полностью убрать прослойку, можно запускать напрямую:
        python bot_new.py        # Linux / macOS
        py -3.13 bot_new.py      # Windows

История исправлений (этот rev):
    • FIX compatibility launcher: `bot_new.py` intentionally owns startup at
      module scope and does not expose `main()`. The old wrapper imported a
      non-existent `bot_new.main`, so `python bot.py` failed after performing
      part of the startup bootstrap. The wrapper now executes `bot_new` with
      `run_name="__main__"`, which is equivalent to the supported direct entry.
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
    """Execute the supported bot_new entrypoint with direct-launch semantics."""
    _print_banner()
    runpy.run_module("bot_new", run_name="__main__")


if __name__ == "__main__":
    main()
