#!/usr/bin/env python3
"""
fix_503_retry.py - добавляет retry с экспоненциальной паузой на 503 UNAVAILABLE,
ПЕРЕД тем как переключать ключ.

Контекст: Gemini free tier для gemini-2.5-flash часто перегружен ("high demand").
503 одинаков на ВСЕХ ключах одного аккаунта, поэтому быстрое переключение ключей
не помогает - нужно подождать и повторить.

Логика после фикса:
- На каждом ключе: до 2 retry с паузой 15 -> 30 сек на 503
- Только если всё равно 503 - переключаем ключ
- После полного круга по ключам - финальная пауза 60 сек и ещё один круг

Также делает скрипт идемпотентным - можно запускать повторно.
"""
from pathlib import Path
import shutil
import sys
import re

TARGET = Path("services/gemini_analyze.py")

if not TARGET.exists():
    print(f"❌ Не найден файл {TARGET}")
    print("   Запускай из корня проекта (где есть папка services/)")
    sys.exit(1)

# backup
backup = TARGET.with_suffix(".py.bak_503_retry")
if not backup.exists():
    shutil.copy2(TARGET, backup)
    print(f"[backup] {backup}")

text = TARGET.read_text(encoding="utf-8")
original = text

# Проверка идемпотентности
if "AUDIT FIX 503-RETRY" in text:
    print("⚠️  Файл уже содержит метку 'AUDIT FIX 503-RETRY' — патч уже применён.")
    print("   Если хочешь переприменить — удали строки с этой меткой вручную или")
    print("   восстанови из backup'а:")
    for b in TARGET.parent.glob("gemini_analyze.py.bak*"):
        print(f"      {b}")
    sys.exit(0)

# ───────────────────────────────────────────────────────────────────────────
# Найти блок с переключением ключей и обернуть его в retry-цикл по 503
# ───────────────────────────────────────────────────────────────────────────
# Ищем строку warning'а и заменяем ВЕСЬ except-блок is_overload_error
# на retry-with-backoff.

# Паттерн (после fix_audio_upload.py версия):
#     if is_quota_error(e) or is_overload_error(e):
#         logger.warning(f"Gemini {'квота' if is_quota_error(e) else '503/disconnect'}: ...")
#         if file_size_mb > 20 and audio_part is not None and hasattr(audio_part, 'name'):
#             asyncio.create_task(_safe_delete_gemini_file(client, audio_part.name))
#         last_err = e
#         break  # следующий клиент

# Мы не меняем логику break — она правильная. Но добавляем retry ВНУТРИ
# attempt-loop (он уже есть — for attempt in range(3)) только для overload_error.

# Стратегия: заменить `break  # следующий клиент` после is_overload_error
# на условный break + sleep+continue на первых попытках.

# Это сложно сделать одной regex-заменой без знания точного отступа,
# поэтому используем построчный анализ.

lines = text.split("\n")
new_lines = []
changes = 0
i = 0

while i < len(lines):
    line = lines[i]
    new_lines.append(line)

    # Ищем строку с warning 503
    if "пробую следующий ключ" in line and "logger.warning" in line:
        # Получим отступ
        m = re.match(r"^(\s*)", line)
        indent = m.group(1) if m else "                        "

        # Найдём в следующих ~6 строках "break  # следующий клиент"
        # и заменим его на условный break (retry на первых попытках)
        for j in range(i + 1, min(i + 8, len(lines))):
            if re.match(r"^\s*break\s*(#.*)?$", lines[j]):
                # Заменяем эту строку на retry-логику
                # attempt — переменная цикла for attempt in range(3) в исходнике
                replacement = [
                    f"{indent}# AUDIT FIX 503-RETRY: на первых попытках ждём и повторяем тем же ключом",
                    f"{indent}if is_overload_error(e) and attempt < 2:",
                    f"{indent}    _wait_503 = 15 * (attempt + 1)  # 15s, 30s",
                    f"{indent}    logger.info(f\"Gemini 503: жду {{_wait_503}}s и повторяю тем же ключом (попытка {{attempt+2}}/3)...\")",
                    f"{indent}    await asyncio.sleep(_wait_503)",
                    f"{indent}    continue  # следующая попытка на ЭТОМ же ключе",
                    f"{indent}break  # все попытки на ключе исчерпаны — следующий клиент",
                ]
                # Вместо одной строки с break вставляем replacement
                new_lines.extend(replacement)
                # Пропускаем оригинальную строку break
                # Но сначала нужно добавить строки между warning и break (last_err = e, asyncio.create_task...)
                # — они УЖЕ добавлены в new_lines, потому что мы идём построчно.
                # Сейчас мы на строке j, lines[i+1..j-1] уже скопированы. lines[j] (break) — пропускаем.
                # Перематываем i на j+1.

                # Однако: lines[i+1..j-1] ещё НЕ в new_lines (мы их не итерировали)
                # — нужно добавить их перед replacement.
                pre = lines[i + 1:j]
                # Уберём последний append (replacement идёт вместо break)
                # Сначала уберём только что добавленный replacement
                for _ in range(len(replacement)):
                    new_lines.pop()
                # Теперь добавим pre, потом replacement
                new_lines.extend(pre)
                new_lines.extend(replacement)
                i = j + 1
                changes += 1
                break
        else:
            i += 1
        continue
    i += 1

text = "\n".join(new_lines)

if changes == 0:
    print("⚠️  Не нашёл паттерн 'пробую следующий ключ' + 'break' рядом.")
    print("   Возможно, fix_audio_upload.py не применялся, или структура другая.")
    print("   Покажи мне строки 150-220 файла services/gemini_analyze.py,")
    print("   я подкорректирую патч под твою структуру.")
    sys.exit(1)

# ───────────────────────────────────────────────────────────────────────────
# Доп. фикс: после полного круга по клиентам (если все упали с overload) -
# финальная пауза 60 сек и ещё один круг.
# ───────────────────────────────────────────────────────────────────────────
# Ищем место "if response is None: raise last_err or RuntimeError(...)"
old_raise = "if response is None:\n            raise last_err or RuntimeError"
new_raise = (
    "# AUDIT FIX 503-RETRY: если все ключи упали с 503, ждём 60s и второй круг\n"
    "        if response is None and last_err is not None and is_overload_error(last_err):\n"
    "            logger.warning(\"Gemini 503 на всех ключах — жду 60s и пробую ещё раз весь круг...\")\n"
    "            await asyncio.sleep(60)\n"
    "            for client in GEMINI_CLIENTS:\n"
    "                try:\n"
    "                    audio_part, used_client = await upload_to_client(client)\n"
    "                    response = await asyncio.wait_for(\n"
    "                        client.aio.models.generate_content(\n"
    "                            model=GEMINI_MODEL,\n"
    "                            contents=[audio_part, prompt],\n"
    "                            config=make_audio_config(temperature=0.1, max_output_tokens=65536),\n"
    "                        ),\n"
    "                        timeout=960.0,\n"
    "                    )\n"
    "                    used_audio_part = audio_part\n"
    "                    logger.info(\"Gemini: второй круг успешен!\")\n"
    "                    break\n"
    "                except Exception as e2:\n"
    "                    logger.warning(f\"Gemini второй круг: {type(e2).__name__}: {str(e2)[:150]}\")\n"
    "                    last_err = e2\n"
    "                    continue\n\n"
    "        if response is None:\n"
    "            raise last_err or RuntimeError"
)

if old_raise in text:
    text = text.replace(old_raise, new_raise, 1)
    print("  [OK] добавлен второй круг по ключам после паузы 60s")
    changes += 1
else:
    print("  [SKIP] паттерн 'if response is None: raise...' не найден — пропускаю второй круг")

# Записываем
if text == original:
    print("\n⚠️  Ничего не изменилось.")
    sys.exit(1)

TARGET.write_text(text, encoding="utf-8")
print(f"\n✅ Готово: {changes} изменений в {TARGET}")
print(f"   Backup: {backup}")
print("\nЛогика теперь такая:")
print("  - На каждом ключе: до 3 попыток с паузой 15s и 30s между 503")
print("  - После круга из 4 ключей с 503: пауза 60s и ещё один полный круг")
print("  - Итого ~5-6 минут терпения на 'high demand', потом fail")
print("\nДальше:")
print("  py -3.13 bot_new.py")
print("\nЕсли ВСЁ РАВНО 503 после 5 минут — значит free tier flash сегодня глухо")
print("перегружен. Тогда варианты:")
print("  1) Сменить модель: в .env поставь GEMINI_MODEL=gemini-2.5-pro (тот же free tier,")
print("     но менее перегружен)")
print("  2) Подождать пиковые часы (Gemini обычно перегружен 14:00-23:00 UTC)")
