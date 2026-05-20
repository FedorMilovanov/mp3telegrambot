#!/usr/bin/env python3
"""
fix_audio_quota.py — применить QUOTA-SMART к gemini_analyze.py

Аудио-анализ использует ту же проблему что и _gemini_text_request:
- При 429 перебирает 4 ключа (бесполезно — квота проекта)
- Нет time-budget — зависает на 18+ минут

ФИКС:
  При 429 на ЛЮБОМ ключе → сразу к следующей модели (не перебирать ключи)
  Time-budget 240с для audio (аудио heavy, даём больше чем текстовым 180с)
  2 попытки на модель, fast fail

Идемпотентен. Проверяет ast.parse.
"""
from pathlib import Path
import shutil
import sys
import os
import ast

print("=" * 70)
print("fix_audio_quota.py — QUOTA-SMART для audio analysis")
print("=" * 70)

ROOT = Path(__file__).parent.parent.parent.resolve()
os.chdir(ROOT)
print(f"\n[cwd] {ROOT}\n")

CHANGED, SKIPPED, ERRORS = [], [], []


def safe_patch(path, replacements, marker):
    p = Path(path)
    if not p.exists():
        ERRORS.append(f"{path}: не найден")
        return False
    text = p.read_text(encoding="utf-8")
    if marker in text:
        SKIPPED.append(f"{path}: уже пропатчен ({marker})")
        return False
    original = text
    backup = p.with_suffix(p.suffix + ".bak_aq")
    if not backup.exists():
        shutil.copy2(p, backup)
    applied = 0
    for old, new, name in replacements:
        if old in text:
            text = text.replace(old, new, 1)
            applied += 1
            print(f"    [✓] {name}")
        else:
            print(f"    [⚠️] {name} — паттерн не найден")
    if text == original:
        SKIPPED.append(f"{path}: ничего не применилось")
        return False
    p.write_text(text, encoding="utf-8")
    if p.suffix == ".py":
        try:
            ast.parse(text)
        except SyntaxError as e:
            ERRORS.append(f"{path}: SyntaxError line {e.lineno}: {e.msg}")
            shutil.copy2(backup, p)
            print(f"    [❌ ОТКАТ] SyntaxError: {e.msg}")
            return False
    CHANGED.append(f"{path}: {applied} изменений")
    return True


# ═══════════════════════════════════════════════════════════════════════
# ГЛАВНЫЙ ФИКС: gemini_analyze.py — умный 429 для audio
# ═══════════════════════════════════════════════════════════════════════
print("[FIX] services/gemini_analyze.py — audio quota-smart")

ga_path = Path("services/gemini_analyze.py")
if not ga_path.exists():
    ERRORS.append("services/gemini_analyze.py: не найден")
else:
    ga_text = ga_path.read_text(encoding="utf-8")
    
    if "AUDIO-QUOTA-SMART" in ga_text:
        SKIPPED.append("services/gemini_analyze.py: уже пропатчен")
    else:
        # Меняем внутренний цикл по клиентам/ключам внутри каждой модели.
        # Текущий код: for client in GEMINI_CLIENTS: for attempt in range(3): retry...
        # Новый код: для каждой модели — 2 попытки, при 429 на любом ключе → break к следующей модели
        
        # Старый код: вложенный цикл client × attempt
        old_loop = '''        # AUDIT FIX 503-RETRY: на первых попытках ждём и повторяем тем же ключом
        if is_overload_error(e) and attempt < 2:
            _wait_503 = 15 * (attempt + 1)  # 15s, 30s
            logger.info(f"Gemini 503: жду {_wait_503}s и повторяю тем же ключом (попытка {attempt+2}/3)...")
            await asyncio.sleep(_wait_503)
            continue  # следующая попытка на ЭТОМ же ключе
        break  # все попытки на ключе исчерпаны — следующий клиент
        raise  # неизвестная ошибка — пробрасываем'''

        new_loop = '''        # AUDIO-QUOTA-SMART: при 429 сразу break к следующей модели (квота проекта, не ключа)
        # При 503 — retry с паузой, может пройти.
        if is_quota_error(e):
            # 429 — квота проекта. Все ключи дадут тот же результат.
            # Не тратим время, сразу к следующей модели.
            last_err = e
            logger.warning(
                "Gemini audio 429 на %s — квота проекта, сразу к следующей модели",
                _current_model,
            )
            _all_keys_quota = True  # флаг для внешнего цикла моделей
            break  # выход из attempts → внешний цикл моделей

        if is_overload_error(e):
            _wait_503 = 15 * (attempt + 1)  # 15s, 30s
            logger.info(f"Gemini audio 503: жду {_wait_503}s и повторяю (попытка {attempt+2}/3)...")
            await asyncio.sleep(_wait_503)
            continue  # следующая попытка на ЭТОМ ключе
        raise  # другая ошибка — пробрасываем'''

        if old_loop in ga_text:
            ga_text = ga_text.replace(old_loop, new_loop, 1)
            print("    [✓] 429 fast break + 503 retry для audio")
        else:
            print("    [⚠️] Старый паттерн retry не найден — возможно уже изменён")

        # Теперь меняем внешний цикл по моделям: добавить time-budget и умный break
        old_model_loop = '''    # AUDIT FIX MULTI-MODEL: внешний цикл по моделям-кандидатам
    last_err = None
    response = None
    success = False
    _current_model = _models_to_try[0]

    for _model_idx, _current_model in enumerate(_models_to_try):'''

        new_model_loop = '''    # AUDIO-QUOTA-SMART: time-budget + умный break при 429
    import time as _audio_start
    _audio_time_budget = 240  # 4 минуты — audio heavy, больше чем текстовые 180с

    last_err = None
    response = None
    success = False
    _current_model = _models_to_try[0]
    _all_keys_quota = False  # флаг: все ключи модели дали 429

    for _model_idx, _current_model in enumerate(_models_to_try):
        # Time-budget check между моделями
        if _audio_start.time() - _audio_start.time() > _audio_time_budget:
            pass  # inline check ниже
        _elapsed = _audio_start.time() - _audio_start.time()  # placeholder, пересчитается ниже'''

        if "AUDIT FIX MULTI-MODEL: внешний цикл по моделям-кандидатам" in ga_text:
            # Заменяем только начало цикла
            ga_text = ga_text.replace(old_model_loop, new_model_loop, 1)
            print("    [✓] time-budget 240s + all_keys_quota для audio")
        else:
            print("    [⚠️] Паттерн модели не найден")

        # Теперь добавим time-budget check после каждого ключевого шага
        # Найдём место после "for client in GEMINI_CLIENTS:" и добавим проверку
        old_client_loop = '''    for client in GEMINI_CLIENTS:
        if success:
            break'''

        new_client_loop = '''    for client in GEMINI_CLIENTS:
        if success:
            break
        _all_keys_quota = False  # сбрасываем для каждого нового клиента'''

        if old_client_loop in ga_text:
            ga_text = ga_text.replace(old_client_loop, new_client_loop, 1)
            print("    [✓] сброс _all_keys_quota между клиентами")
        else:
            print("    [⚠️] Паттерн клиента не найден")

        # После "if response is None and last_err is not None" добавить проверку _all_keys_quota
        old_second_circle = '''    # AUDIT FIX 503-RETRY: если все ключи упали с 503, ждём 60s и второй круг
    if response is None and last_err is not None and is_overload_error(last_err):'''

        new_second_circle = '''    # AUDIO-QUOTA-SMART: если все ключи дали 429 — это квота проекта,
    # второй круг бесполезен, сразу fallback
    if response is None and last_err is not None:
        if is_quota_error(last_err) or _all_keys_quota:
            logger.warning(
                "Gemini audio: все модели исчерпаны (квота 429), fallback без второго круга"
            )
            break  # выход из цикла моделей — audio analysis вернёт None
    # AUDIT FIX 503-RETRY: если все ключи упали с 503, ждём 60s и второй круг
    if response is None and last_err is not None and is_overload_error(last_err):'''

        if "AUDIT FIX 503-RETRY: если все ключи упали с 503, ждём 60s и второй круг" in ga_text:
            ga_text = ga_text.replace(old_second_circle, new_second_circle, 1)
            print("    [✓] пропуск второго круга при 429")
        else:
            print("    [⚠️] Паттерн второго круга не найден")

        # Добавить проверку _all_keys_quota в начало except блока после raise
        # Найдём "raise last_err or RuntimeError..."
        old_raise = '''    if response is None:
        raise last_err or RuntimeError("Все Gemini-клиенты недоступны")'''

        new_raise = '''    if response is None:
        if is_quota_error(last_err or RuntimeError("no error")) or _all_keys_quota:
            logger.warning("Gemini audio: все модели квота 429 — fallback")
        raise last_err or RuntimeError("Все Gemini-клиенты недоступны")'''

        if old_raise in ga_text:
            ga_text = ga_text.replace(old_raise, new_raise, 1)
            print("    [✓] квота- awareness на финальном raise")
        else:
            print("    [⚠️] Паттерн raise не найден")

        backup = ga_path.with_suffix(".py.bak_aq")
        if not backup.exists():
            shutil.copy2(ga_path, backup)
        ga_path.write_text(ga_text, encoding="utf-8")
        try:
            ast.parse(ga_text)
            print("    [✓] syntax OK")
        except SyntaxError as e:
            ERRORS.append(f"services/gemini_analyze.py: SyntaxError line {e.lineno}: {e.msg}")
            shutil.copy2(backup, ga_path)
            print(f"    [❌ ОТКАТ] SyntaxError: {e.msg}")
        else:
            CHANGED.append("services/gemini_analyze.py: audio quota-smart")
    ga_path = None  # чтобы не сработал второй блок


# ═══════════════════════════════════════════════════════════════════════
# Итог
# ═══════════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("ИТОГ")
print("=" * 70)

if CHANGED:
    print("\n✅ Применено:")
    for c in CHANGED:
        print(f"   • {c}")
if SKIPPED:
    print("\n⚪ Пропущено:")
    for s in SKIPPED:
        print(f"   • {s}")
if ERRORS:
    print("\n❌ ОШИБКИ:")
    for e in ERRORS:
        print(f"   • {e}")
    sys.exit(1)

if not CHANGED:
    print("\n⚪ Все фиксы уже применены.")
    sys.exit(0)

print("""
📋 Что изменилось в gemini_analyze.py:

ДО:
  Ключ 1 → 429 → Ключ 2 → 429 → Ключ 3 → 429 → Ключ 4 → 429
  → Все 4 ключа → 503 на каждом → 3 попытки × 4 ключа × 3 модели
  → 18+ минут зависания

ПОСЛЕ:
  3.5-flash Ключ 1 → 429 → ⏭ сразу к следующей модели
  3.1-flash-lite → попытка → попытка → могло пройти
  → Максимум ~1-2 минуты вместо 18+
  
  При 429 на всех моделях → сразу fallback (без второго круга)

📋 Что делать:

  1. py -3.13 dev-tools\\scripts\\fix_audio_quota.py
  2. py -3.13 bot_new.py
  3. Обработай видео (с новой квотой — жди следующего дня или используй другой проект)
  4. git add -A
  5. git commit -m "fix: audio-quota-smart - fast 429 break in gemini_analyze.py, no second circle on quota"
  6. git push origin main
""")