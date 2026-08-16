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


def remove_runtime_feature(text: str, feature_id: str) -> str:
    quoted = r"[\"']" + re.escape(feature_id) + r"[\"']"
    pattern = re.compile(r"\n    RuntimeFeature\(\n        " + quoted + r",.*?\n    \),", re.DOTALL)
    text2, count = pattern.subn("", text, count=1)
    if count != 1:
        raise RuntimeError(f"runtime manifest feature not found exactly once: {feature_id}")
    print(f"removed runtime manifest feature: {feature_id}")
    return text2


def wave4() -> None:
    commands_path = "handlers/commands.py"
    commands = read(commands_path)
    help_pattern = re.compile(
        r"async def help_command\(update, context\):\n.*?(?=\n\ndef _extract_yt_id_from_text)",
        re.DOTALL,
    )
    help_new = '''async def help_command(update, context) -> None:\n    """Describe the actual source-owned LiveDub delivery contract."""\n    user_id = update.effective_user.id\n    limit_line = (\n        "👑 VIP — без ограничений"\n        if user_id in WHITELIST_IDS\n        else f"📵 {DAILY_LIMIT} видео/день • 1 запрос/мин"\n    )\n    audio_set = "видео с переводом + чистый русский MP3 + финальный объединённый MP3"\n    text = (\n        "ℹ️ <b>Помощь</b>\\n\\n"\n        "Отправьте ссылку на видео или плейлист.\\n\\n"\n        "🇷🇺 <b>Русский режим</b>\\n"\n        "MP3, тема, таймкоды, конспект и дополнительные материалы.\\n\\n"\n        "🇬🇧 <b>ENG Full</b>\\n"\n        f"Полный анализ + {audio_set} + смысловая проверка перевода.\\n\\n"\n        "⚡ <b>ENG Quick</b>\\n"\n        f"{audio_set}. Без конспекта и смысловой QA.\\n\\n"\n        "⚡🔍 <b>ENG Quick QA</b>\\n"\n        f"{audio_set} + лёгкая проверка коротких роликов.\\n\\n"\n        f"🔒 Ваши лимиты: {limit_line}\\n\\n"\n        "/start — приветствие\\n"\n        "/help — эта справка\\n"\n        "/mode — выбор режима\\n"\n        "/archive — последние публикации\\n"\n        "/search &lt;текст&gt; — поиск по архиву\\n"\n        "/segments — список сегментов\\n"\n        "/cut — вырезать сегмент\\n\\n"\n        "🔑 Для стабильных живых голосов требуется VOT_API_TOKEN "\n        "или YANDEX_OAUTH_TOKEN в .env."\n    )\n    await update.message.reply_text(text, parse_mode="HTML")\n'''
    commands, count = help_pattern.subn(lambda _match: help_new, commands, count=1)
    if count != 1:
        raise RuntimeError("help_command anchor missing")

    status_anchor = '''    await update.message.reply_text(_html_message_limit("\\n".join(lines)), parse_mode="HTML")\n\n\nasync def metrics_command'''
    status_new = '''    from services.operator_runtime_status import safe_operator_runtime_status_html_lines\n    lines.extend(safe_operator_runtime_status_html_lines())\n    await update.message.reply_text(_html_message_limit("\\n".join(lines)), parse_mode="HTML")\n\n\nasync def metrics_command'''
    if status_anchor not in commands:
        raise RuntimeError("status reply anchor missing")
    commands = commands.replace(status_anchor, status_new, 1)
    write(commands_path, commands)

    operator_path = "services/operator_runtime_status.py"
    operator = read(operator_path)
    operator = operator.replace("import functools\n", "")
    operator = operator.replace("import threading\n", "")
    operator = operator.replace("from types import ModuleType\n", "")
    operator = operator.replace("_INSTALLED = False\n_LOCK = threading.Lock()\n", "")
    marker = "\n\nclass _ReplyCaptureMessage:"
    if marker not in operator:
        raise RuntimeError("operator status runtime patch marker missing")
    operator = operator.split(marker, 1)[0].rstrip() + '''\n\n\n__all__ = [\n    "OPERATOR_RUNTIME_STATUS_POLICY",\n    "operator_runtime_status_html_lines",\n    "operator_runtime_status_payload",\n    "safe_operator_runtime_status_html_lines",\n]\n'''
    write(operator_path, operator)

    restart_path = "services/restart_state_runtime.py"
    restart = read(restart_path)
    restart = restart.replace("import logging\n", "")
    restart = restart.replace("import threading\n", "")
    restart = restart.replace("from types import ModuleType\n", "")
    restart = restart.replace("logger = logging.getLogger(__name__)\n_LOCK = threading.Lock()\n_INSTALLED = False\n\n", "")
    install_pattern = re.compile(
        r"\n\ndef install_restart_state_runtime\(main_module: ModuleType\) -> None:\n.*?(?=\n\n__all__ =)",
        re.DOTALL,
    )
    restart, count = install_pattern.subn("", restart, count=1)
    if count != 1:
        raise RuntimeError("restart-state installer anchor missing")
    restart = restart.replace('    "install_restart_state_runtime",\n', "")
    write(restart_path, restart)

    manifest_path = "services/runtime_manifest.py"
    manifest = read(manifest_path)
    for feature in (
        "gemini-startup-diagnostics",
        "livedub-help",
        "restart-state-runtime",
    ):
        manifest = remove_runtime_feature(manifest, feature)
    write(manifest_path, manifest)

    for dead in (
        "services/livedub_help_runtime.py",
        "services/gemini_startup_diagnostics.py",
    ):
        target = ROOT / dead
        if not target.exists():
            raise RuntimeError(f"expected legacy runtime file missing: {dead}")
        target.unlink()
        print(f"deleted {dead}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("wave", choices=("wave4",))
    parser.parse_args()
    wave4()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
