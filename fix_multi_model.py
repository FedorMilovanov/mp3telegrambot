#!/usr/bin/env python3
"""
fix_multi_model.py - Multi-model fallback для МАКСИМАЛЬНОГО КАЧЕСТВА анализа.

Юзкейс: 1-5 запросов в день для серьёзного контента (посты в TG).
Главное — качество и надёжность ОДНОГО запроса, а не пропускная способность.

Список fallback (по убыванию качества, только бесплатные с аудио):
  1. GEMINI_MODEL из .env (по умолчанию gemini-3.5-flash)
  2. gemini-3-flash         -- запас на случай 503
  3. gemini-3.1-flash-lite  -- последний шанс (500 RPD, точно прорвётся)

ИСКЛЮЧЕНО:
  - gemini-2.5-flash, 2.5-flash-lite: слабее по интеллекту, чем 3.x — нет смысла
  - gemini-2.5-pro, 3.1-pro: free quota = 0/0 (по dashboard'у)
  - gemini-2-*: deprecated

Идемпотентен. Проверяет синтаксис.
"""
from pathlib import Path
import shutil
import sys

TARGET = Path("services/gemini_analyze.py")

if not TARGET.exists():
    print(f"❌ Не найден файл {TARGET}")
    sys.exit(1)

backup = TARGET.with_suffix(".py.bak_multi_model")
if not backup.exists():
    shutil.copy2(TARGET, backup)
    print(f"[backup] {backup}")

text = TARGET.read_text(encoding="utf-8")
original = text

if "AUDIT FIX MULTI-MODEL" in text:
    print("⚠️  Файл уже содержит 'AUDIT FIX MULTI-MODEL'.")
    print("   Откати из backup'а и переприменяй:")
    for b in TARGET.parent.glob("gemini_analyze.py.bak*"):
        print(f"     Copy-Item {b} {TARGET} -Force")
    sys.exit(0)

# ─────────────────────────────────────────────────────────────────────────
# 1) Список моделей-кандидатов (приоритет КАЧЕСТВА)
# ─────────────────────────────────────────────────────────────────────────
marker1 = """    if not GEMINI_CLIENTS:
        return None, None, None"""

inject1 = """    if not GEMINI_CLIENTS:
        return None, None, None

    # AUDIT FIX MULTI-MODEL: fallback по качеству (для редких но мощных запросов)
    # Юзкейс: 1-5 видео/день, главное — качество анализа
    _user_model = GEMINI_MODEL
    _fallback_models = [
        _user_model,                # из .env, рекомендуется gemini-3.5-flash
        "gemini-3-flash",            # 5 RPM / 20 RPD — тоже свежая 3.x
        "gemini-3.1-flash-lite",     # 15 RPM / 500 RPD — последний шанс, точно прорвётся
    ]
    _seen = set()
    _models_to_try = []
    for _m in _fallback_models:
        if _m and _m not in _seen:
            _seen.add(_m)
            _models_to_try.append(_m)
    logger.info(f"Gemini multi-model fallback (приоритет качества): {_models_to_try}")"""

if marker1 not in text:
    print("❌ Не найден маркер 'if not GEMINI_CLIENTS'")
    sys.exit(1)

text = text.replace(marker1, inject1, 1)
print("  [OK 1/5] список моделей по приоритету качества")

# ─────────────────────────────────────────────────────────────────────────
# 2) GEMINI_MODEL → _current_model в _do_generate
# ─────────────────────────────────────────────────────────────────────────
old_use1 = "                            model=GEMINI_MODEL,\n                            contents=[_ap, prompt],"
new_use1 = "                            model=_current_model,  # AUDIT FIX MULTI-MODEL\n                            contents=[_ap, prompt],"
if old_use1 in text:
    text = text.replace(old_use1, new_use1, 1)
    print("  [OK 2/5] _do_generate: model=_current_model")
else:
    print("  [SKIP 2/5] паттерн _do_generate не найден")

# ─────────────────────────────────────────────────────────────────────────
# 3) GEMINI_MODEL → _current_model во втором круге
# ─────────────────────────────────────────────────────────────────────────
old_use2 = "                        model=GEMINI_MODEL,\n                        contents=[audio_part, prompt],"
new_use2 = "                        model=_current_model,  # AUDIT FIX MULTI-MODEL\n                        contents=[audio_part, prompt],"
if old_use2 in text:
    text = text.replace(old_use2, new_use2, 1)
    print("  [OK 3/5] второй круг: model=_current_model")
else:
    print("  [SKIP 3/5] паттерн второго круга не найден")

# ─────────────────────────────────────────────────────────────────────────
# 4) Внешний цикл по моделям
# ─────────────────────────────────────────────────────────────────────────
old_block_start = """        # Для каждого клиента загружаем файл отдельно (файлы не переносятся между ключами)
        last_err = None
        response = None
        success = False"""

new_block_start = """        # AUDIT FIX MULTI-MODEL: внешний цикл по моделям-кандидатам
        last_err = None
        response = None
        success = False
        _current_model = _models_to_try[0]

        for _model_idx, _current_model in enumerate(_models_to_try):
            if success:
                break
            if _model_idx > 0:
                logger.warning(
                    f"Gemini переключаюсь на резервную модель: {_current_model} "
                    f"(модель #{_model_idx+1}/{len(_models_to_try)})"
                )
            # Для каждого клиента загружаем файл отдельно (файлы не переносятся между ключами)
            last_err = None
            response = None
            success = False"""

if old_block_start in text:
    text = text.replace(old_block_start, new_block_start, 1)
    print("  [OK 4/5] внешний цикл по моделям добавлен")
else:
    print("  [SKIP 4/5] блок инициализации не найден")
    print("   Сначала применяй fix_503_retry.py!")
    sys.exit(1)

# ─────────────────────────────────────────────────────────────────────────
# 5) Сдвиг отступов внутреннего блока на +4 пробела
# ─────────────────────────────────────────────────────────────────────────
lines = text.split("\n")
new_lines = []
i = 0
in_block = False
indent_start = "        for client in GEMINI_CLIENTS:"
indent_end = "        if response is None:"
shifted = 0

while i < len(lines):
    line = lines[i]
    if not in_block and line.rstrip() == indent_start:
        in_block = True
        new_lines.append("    " + line)
        shifted += 1
        i += 1
        continue
    if in_block:
        if line.rstrip() == indent_end:
            in_block = False
            new_lines.append(line)
            i += 1
            continue
        if line.strip() == "":
            new_lines.append(line)
        else:
            new_lines.append("    " + line)
            shifted += 1
        i += 1
        continue
    new_lines.append(line)
    i += 1

if shifted > 0:
    text = "\n".join(new_lines)
    print(f"  [OK 5/5] сдвинут отступ у {shifted} строк")
else:
    print("  [SKIP 5/5] не удалось сдвинуть отступ")

if text == original:
    print("\n⚠️  Никаких изменений не внесено.")
    sys.exit(1)

TARGET.write_text(text, encoding="utf-8")
print(f"\n✅ Записано в {TARGET}")
print(f"   Backup: {backup}")

# Проверка синтаксиса
print("\n[syntax-check] Проверяю Python-синтаксис...")
import ast
try:
    ast.parse(text)
    print("  ✅ SYNTAX OK")
except SyntaxError as e:
    print(f"  ❌ SYNTAX ERROR: line {e.lineno}: {e.msg}")
    print(f"\n  ⚠️  Откат:")
    print(f"     Copy-Item {backup} {TARGET} -Force")
    sys.exit(1)

print("\n📋 Логика после фикса:")
print("  Модель 1: твоя из .env (рекомендуется gemini-3.5-flash)")
print("    × 4 ключа × 3 попытки × 2 круга = ~7 минут retry")
print("    ↓ если всё упало")
print("  Модель 2: gemini-3-flash (запас)")
print("    × тот же цикл retry")
print("    ↓")
print("  Модель 3: gemini-3.1-flash-lite (500 RPD — точно прорвётся)")
print("    × тот же цикл retry")
print("\n📌 Следующий шаг:")
print("  1. В .env поменяй:  GEMINI_MODEL=gemini-3.5-flash")
print("  2. py -3.13 bot_new.py")
print("\n💡 С таким количеством запросов (1-5/день) free-квоты хватит с запасом ×20")
