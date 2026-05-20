#!/usr/bin/env python3
"""
fix_audio_upload.py - заставляет gemini_analyze_audio ВСЕГДА использовать
Gemini File API (upload) вместо inline base64 для аудио.

Проблема: inline-передача mp3 через Part.from_bytes превращает файл в base64
(+33% размер) и часто превышает лимит request size (~20MB), вызывая разрыв
соединения (RemoteProtocolError), который is_overload_error трактует как 503.

Решение: всегда загружать через File API upload, независимо от размера файла.
"""
from pathlib import Path
import shutil
import sys

TARGET = Path("services/gemini_analyze.py")

if not TARGET.exists():
    print(f"❌ Не найден файл {TARGET}")
    print("   Запускай скрипт из корня проекта (где есть папка services/)")
    sys.exit(1)

# backup
backup = TARGET.with_suffix(".py.bak_audio_upload")
if not backup.exists():
    shutil.copy2(TARGET, backup)
    print(f"[backup] {backup}")

text = TARGET.read_text(encoding="utf-8")
original = text
changes = 0

# 1. Не читаем audio_bytes заранее (экономим память)
old1 = "audio_bytes = mp3_path.read_bytes() if file_size_mb <= 20 else None"
new1 = "audio_bytes = None  # AUDIT FIX: всегда upload (inline отключён, чтобы избежать 503 на 20+MB base64)"
if old1 in text:
    text = text.replace(old1, new1, 1)
    print("  [OK 1/3] audio_bytes = None")
    changes += 1
else:
    print("  [SKIP 1/3] pattern 'audio_bytes = mp3_path.read_bytes()...' not found")

# 2. В upload_to_client всегда идём в ветку upload
old2 = "if file_size_mb > 20:"
new2 = "if file_size_mb > 0:  # AUDIT FIX: всегда upload (inline ломается на больших base64)"
if old2 in text:
    text = text.replace(old2, new2, 1)
    print("  [OK 2/3] upload threshold changed: > 20 -> > 0")
    changes += 1
else:
    print("  [SKIP 2/3] pattern 'if file_size_mb > 20:' not found")

# 3. Добавим детальное логирование ошибки в _gemini_call_with_retry / fallback
# (не критично, но поможет диагностике если ещё что-то сломается)
old3 = '''logger.warning(f"Gemini {'квота' if is_quota_error(e) else '503/disconnect'}, пробую следующий ключ...")'''
new3 = '''logger.warning(f"Gemini {'квота' if is_quota_error(e) else '503/disconnect'}: {type(e).__name__}: {str(e)[:200]} -- пробую следующий ключ...")'''
if old3 in text:
    text = text.replace(old3, new3, 1)
    print("  [OK 3/3] error logging enriched with type+message")
    changes += 1
else:
    print("  [SKIP 3/3] error-logging pattern not found (возможно, уже патчили)")

if text == original:
    print("\n⚠️  Никаких изменений не внесено. Возможные причины:")
    print("   - файл уже пропатчен")
    print("   - структура services/gemini_analyze.py изменилась")
    print("   - проверь, что запускаешь скрипт из корня проекта")
else:
    TARGET.write_text(text, encoding="utf-8")
    print(f"\n✅ Готово: {changes} изменений записано в {TARGET}")
    print(f"   Backup: {backup}")
    print("\nДальше:")
    print("   1) Перезапусти бота через .bat или: py -3.13 bot_new.py")
    print("   2) Скинь лог при обработке нового видео")
