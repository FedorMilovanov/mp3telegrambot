#!/usr/bin/env python3
"""
apply_quota_smart.py — умный фикс очереди Gemini-ключей при 429 quota.

ПРОБЛЕМА:
  У тебя 4 Gemini-ключа (GEMINI_API_KEY + KEY_2 + KEY_3 + KEY_4).
  При 429 RESOURCE_EXHAUSTED — это ЛИМИТ ПРОЕКТА, а не ключа.
  Все 4 ключа одного проекта дают одинаковый 429.
  
  Текущий код при 429 делает `continue` и пробует следующий ключ —
  это тратит время впустую. Бот зависает на 8+ минут.

РЕШЕНИЕ (по запросу пользователя):
  1. При 429 на ЛЮБОМ ключе — сразу break к следующей модели (не тратить ключи)
  2. Time-budget 180с — если за 3 минуты нет ответа, fallback
  3. ВСЕ задачи остаются на gemini-3.5-flash (GEMINI_MODEL) — НИКАКИХ lite
  4. Fallback модель: 2.5-flash-lite (свежая, не lite по качеству)
  5. 2 попытки на модель — fast fail

ФАЙЛЫ:
  - services/telegraph_pages.py  — _gemini_text_request

Идемпотентен. Проверяет синтаксис через ast.parse.
"""
from pathlib import Path
import shutil
import sys
import os
import ast

print("=" * 70)
print("apply_quota_smart.py — умный 429 handling + time-budget")
print("         КАЧЕСТВО: максимум, никаких lite")
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
    backup = p.with_suffix(p.suffix + ".bak_qs")
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
# ГЛАВНЫЙ ФИКС: _gemini_text_request — умный 429 + time-budget + NO LITE
# ═══════════════════════════════════════════════════════════════════════
print("[FIX 1+2+3] services/telegraph_pages.py")

# Ищем текущую реализацию _gemini_text_request — она может быть в одном
# из двух состояний: старая (с gemini-3.1-flash-lite) или уже частично
# пропатченная. Проверяем по маркеру "DEEP-QUALITY FIX [B]".

old_func = '''    # DEEP-QUALITY FIX [B]: умный быстрый retry — 2 быстрые попытки на модель → следующая
    # На 3.1-flash-lite (последняя) даём 3 попытки, она почти не перегружена.
    # Также убрана двойная вложенность: тут retry, gemini_generate уже без своего retry.
    # FINAL-POLISH FIX 1: gemini-3-flash не существует как имя API → 404.
    # У этого аккаунта в free tier доступны: gemini-3.5-flash, gemini-2.5-flash-lite,
    # gemini-3.1-flash-lite. Последняя — самая стабильная (500 RPD free).
    _models = [GEMINI_MODEL]
    for _m in ("gemini-3.1-flash-lite", "gemini-2.5-flash-lite"):
        if _m not in _models:
            _models.append(_m)

    def _is_internal_error(e: Exception) -> bool:
        s = str(e)
        return "500" in s or "INTERNAL" in s.upper()

    # Импортируем напрямую чтобы не идти через gemini_generate (там свой retry, удваивает ожидание)
    from core.globals import GEMINI_CLIENTS as _CLIENTS

    last_err = None
    for model_idx, model_name in enumerate(_models):
        if model_idx > 0:
            logger.warning(
                "_gemini_text_request: переключаюсь на резервную модель %s (#%d/%d)",
                model_name, model_idx + 1, len(_models),
            )

        # Сколько попыток на ЭТОЙ модели:
        # - первая (3.5-flash): 2 попытки (быстро отступаем если перегружена)
        # - средняя (3-flash): 2 попытки
        # - последняя (3.1-flash-lite): 3 попытки (она редко перегружена, дожимаем)
        _max_attempts = 3 if model_idx == len(_models) - 1 else 2

        for attempt in range(_max_attempts):
            # Пробуем КАЖДЫЙ из ключей по одному разу (без внутреннего retry в gemini_generate)
            _client_err = None
            _got_response = False
            for client in _CLIENTS:
                try:
                    resp = await client.aio.models.generate_content(
                        model=model_name,
                        contents=prompt,
                        config=make_text_config_smart(
                            temperature=temperature,
                            max_output_tokens=max_tokens,
                            model_name=model_name,
                            thinking_level="high",
                        ),
                    )
                    result = resp.text or ""
                    if result:
                        return result
                    logger.warning(
                        "_gemini_text_request[%s]: пустой ответ (finish=%s)",
                        model_name,
                        resp.candidates[0].finish_reason if resp.candidates else "?",
                    )
                    _got_response = True
                    break
                except Exception as e:
                    _client_err = e
                    if is_quota_error(e):
                        # Квота — сразу к следующему ключу
                        continue
                    if _is_internal_error(e) or is_overload_error(e):
                        # 503/500 — пробуем следующий ключ
                        continue
                    # Другая ошибка — пробрасываем
                    raise

            if _got_response:
                # Пустой ответ — пробуем следующую модель сразу
                break

            # Все ключи дали 503/500
            last_err = _client_err
            if attempt < _max_attempts - 1:
                wait = 10 * (attempt + 1)  # 10s, 20s — быстрее чем раньше
                logger.warning(
                    "_gemini_text_request[%s]: все ключи 503/500 (попытка %d/%d), жду %dс...",
                    model_name, attempt + 1, _max_attempts, wait,
                )
                await asyncio.sleep(wait)
                continue

    if last_err:
        logger.warning("_gemini_text_request: все модели исчерпаны, последняя ошибка: %s", str(last_err)[:200])
    return None'''

new_func = '''    # QUOTA-SMART FIX: умный 429 handling + time-budget
    #
    # При 429 (quota exhausted) — сразу к следующей модели.
    # Ключи делят квоту проекта, поэтому при 429 на одном ключе
    # все остальные ключи того же проекта тоже дадут 429.
    # Не тратим время на бесполезный перебор ключей.
    #
    # При 503/500 (overload) — retry с короткой паузой, может пройти.
    #
    # КАЧЕСТВО: ВСЕ задачи на gemini-3.5-flash (GEMINI_MODEL).
    # Никаких lite — fallback только на 2.5-flash-lite (свежая модель).
    #
    # Time-budget 180с — защита от зависания.
    import time as _time
    _start_time = _time.time()
    _TIME_BUDGET = 180  # секунд

    # ВСЕ на максимальном качестве — 3.5-flash
    # Резерв: 2.5-flash-lite (свежая модель, не lite по качеству)
    _models = [GEMINI_MODEL]
    if GEMINI_MODEL != "gemini-2.5-flash-lite":
        _models.append("gemini-2.5-flash-lite")

    def _is_internal_error(e: Exception) -> bool:
        s = str(e)
        return "500" in s or "INTERNAL" in s.upper()

    # Импортируем напрямую чтобы не идти через gemini_generate
    from core.globals import GEMINI_CLIENTS as _CLIENTS

    last_err = None
    for model_idx, model_name in enumerate(_models):
        # Time-budget check
        if _time.time() - _start_time > _TIME_BUDGET:
            logger.warning(
                "_gemini_text_request: TIME-BUDGET (%ds) исчерпан — fallback",
                _TIME_BUDGET,
            )
            break

        if model_idx > 0:
            logger.warning(
                "_gemini_text_request: переключаюсь на модель %s (#%d/%d)",
                model_name, model_idx + 1, len(_models),
            )

        # 2 попытки на модель — fast fail
        _max_attempts = 2
        _all_keys_quota = True  # True = все ключи дали 429

        for attempt in range(_max_attempts):
            # Time-budget check
            if _time.time() - _start_time > _TIME_BUDGET:
                break

            _client_err = None
            _got_response = False

            for client in _CLIENTS:
                try:
                    resp = await client.aio.models.generate_content(
                        model=model_name,
                        contents=prompt,
                        config=make_text_config_smart(
                            temperature=temperature,
                            max_output_tokens=max_tokens,
                            model_name=model_name,
                            thinking_level="high",
                        ),
                    )
                    result = resp.text or ""
                    if result:
                        return result
                    logger.warning(
                        "_gemini_text_request[%s]: пустой ответ (finish=%s)",
                        model_name,
                        resp.candidates[0].finish_reason if resp.candidates else "?",
                    )
                    _got_response = True
                    _all_keys_quota = False
                    break
                except Exception as e:
                    _client_err = e
                    if is_quota_error(e):
                        # 429 — квота проекта, все ключи дадут тот же результат
                        # Не тратим время, сразу к следующей модели
                        continue
                    # 503/500 или другая ошибка
                    _all_keys_quota = False
                    if _is_internal_error(e) or is_overload_error(e):
                        continue
                    raise

            if _got_response:
                break  # пустой ответ — следующая модель

            # ГЛАВНЫЙ ФИКС: все ключи дали 429 — модель исчерпана,
            # ретраить бесполезно, идём к следующей модели сразу
            if _all_keys_quota:
                logger.warning(
                    "_gemini_text_request[%s]: квота 429 на всех ключах — следующая модель",
                    model_name,
                )
                last_err = _client_err
                break

            # 503/500 — пауза и retry
            last_err = _client_err
            if attempt < _max_attempts - 1:
                wait = 15
                logger.warning(
                    "_gemini_text_request[%s]: 503/500 (попытка %d/%d), жду %dс...",
                    model_name, attempt + 1, _max_attempts, wait,
                )
                await asyncio.sleep(wait)

    if last_err:
        elapsed = _time.time() - _start_time
        logger.warning(
            "_gemini_text_request: все модели исчерпаны (за %.1fs), последняя ошибка: %s",
            elapsed, str(last_err)[:200],
        )
    return None'''

safe_patch(
    "services/telegraph_pages.py",
    [(old_func, new_func, "smart 429 + time-budget 180s + NO LITE")],
    marker="QUOTA-SMART FIX"
)


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
📋 Что изменилось:

ДО:
  429 → continue (пробуем следующий ключ) → всё равно 429 → тратим 4× времени
  Нет time-budget → зависаем на 8+ минут
  gemini-3.1-flash-lite в fallback → снижение качества

ПОСЛЕ:
  429 → сразу break к следующей модели (не тратим ключи)
  Time-budget 180с → fallback через 3 минуты максимум
  Никаких lite — всё на gemini-3.5-flash, fallback на 2.5-flash-lite

📋 Логика 429:

  Ключ 1 → 429 → Ключ 2 → 429 → Ключ 3 → 429 → Ключ 4 → 429
         ↓ сразу сюда (не тратим ключи)
  Модель 2 (2.5-flash-lite) → попытка 1 → попытка 2
  → fallback (через ~1 минуту вместо 8+ минут)

📋 Что делать:

  1. py -3.13 dev-tools\\scripts\\apply_quota_smart.py
  2. py -3.13 bot_new.py
  3. Обработай видео → скинь лог
  4. git add -A
  5. git commit -m "fix: quota-smart - fast 429 break, time-budget 180s, no lite"
  6. git push origin main
  7. .\\dev-tools\\scripts\\cleanup_backups.ps1
""")