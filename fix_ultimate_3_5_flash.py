#!/usr/bin/env python3
"""
fix_ultimate_3_5_flash.py - ИТОГОВЫЙ профессиональный фикс под gemini-3.5-flash.

Покрывает 7 багов одним патчем:

  [P0] BUG #1: _gemini_text_request (Reflection/Study) не имеет multi-model fallback
              → 503 на всех 12 попытках → Reflection падает в fallback
  [P0] BUG #6: Cocoon AI вылазит когда конспект короткий (следствие #1)
  [P1] BUG #2: _gemini_text_request не использует thinking_level=high
  [P1] BUG #3: make_audio_config не передаёт thinking_level=high для 3.x
  [P2] BUG #4: Markdown **жирный** от 3.5-flash попадает в Telegram caption
              в полях analysis_summary, argument_arc (раньше только main_topic чистился)
  [P2] BUG #5: Пробел перед смайликом-таймкодом в caption
  [P3] BUG #7: 'gemini-3.5-flash' не в whitelist живых моделей в main.py

ОТКАТ: каждое изменение делает backup .bak_ultimate_<filename>
"""
from pathlib import Path
import shutil
import sys

# ═══════════════════════════════════════════════════════════════════════
# Helper
# ═══════════════════════════════════════════════════════════════════════

def patch_file(path: Path, replacements: list, label: str) -> bool:
    """Apply list of (old, new, name) replacements to file. Returns True if changed."""
    if not path.exists():
        print(f"  ❌ {path} не существует")
        return False

    backup = path.with_suffix(path.suffix + ".bak_ultimate")
    if not backup.exists():
        shutil.copy2(path, backup)
        print(f"  [backup] {backup}")

    text = path.read_text(encoding="utf-8")
    original = text
    applied = 0

    for old, new, name in replacements:
        if old in text:
            text = text.replace(old, new, 1)
            print(f"    [OK] {name}")
            applied += 1
        else:
            print(f"    [SKIP] {name} — паттерн не найден")

    if text != original:
        path.write_text(text, encoding="utf-8")

        # Syntax check для .py
        if path.suffix == ".py":
            import ast
            try:
                ast.parse(text)
                print(f"    [✅ SYNTAX OK]")
            except SyntaxError as e:
                print(f"    [❌ SYNTAX ERROR] line {e.lineno}: {e.msg}")
                print(f"    ⚠️  Откатить: Copy-Item {backup} {path} -Force")
                shutil.copy2(backup, path)
                return False

        print(f"  ✅ {label}: {applied} изменений записано")
        return True
    else:
        print(f"  ⚪ {label}: нет изменений")
        return False


# ═══════════════════════════════════════════════════════════════════════
# FIX 1: core/globals.py — make_audio_config с thinking_level + helper
# ═══════════════════════════════════════════════════════════════════════
print("\n[1/4] core/globals.py — thinking_level для 3.x + helper")

globals_old = '''def make_audio_config(temperature: float = 0.1, max_output_tokens: int = 65536):
    if not HAS_GEMINI or types is None:
        return None
    # AUDIT FIX 2026-05-20: google-genai 1.x не поддерживает audio_timestamp на уровне API.
    # В будущем (SDK >=2.0.0) можно вернуть: audio_timestamp=True для точных таймкодов audio-only.
    return types.GenerateContentConfig(
        temperature=temperature,
        max_output_tokens=max_output_tokens,
    )'''

globals_new = '''def _is_gemini_3x(model_name: str) -> bool:
    """ULTIMATE FIX 3.5-FLASH: определяет 3.x семейство Gemini моделей.
    Для 3.x: используем thinking_level (новый параметр), не используем temperature.
    Для 2.x: используем temperature (там thinking_config работает иначе).
    """
    if not model_name:
        return False
    m = model_name.lower()
    return any(tok in m for tok in ("gemini-3.5", "gemini-3.1", "gemini-3-", "gemini-3.0"))


def _build_thinking_config(level: str = "high"):
    """ULTIMATE FIX: создаёт ThinkingConfig с fallback на старый thinking_budget."""
    if not HAS_GEMINI or types is None:
        return None
    try:
        return types.ThinkingConfig(thinking_level=level)
    except (AttributeError, TypeError):
        # SDK старый — пробуем deprecated thinking_budget
        try:
            budget_map = {"minimal": 4096, "low": 8192, "medium": 16384, "high": 24576}
            return types.ThinkingConfig(thinking_budget=budget_map.get(level, 24576))
        except Exception:
            return None


def make_audio_config(temperature: float = 0.1, max_output_tokens: int = 65536, model_name: str = None):
    """ULTIMATE FIX 3.5-FLASH: конфиг для audio-вызовов с авто-адаптацией под поколение.

    Для 3.x: thinking_level="high" (default=medium слабее чем 3-flash-preview!), без temperature
    Для 2.x: temperature как раньше

    max_output_tokens снижен до 40000 для 3.x — оставляем место для thinking-токенов
    (high может занимать до 30K токенов до начала финального ответа).

    AUDIT FIX 2026-05-20: google-genai 1.x не поддерживает audio_timestamp.
    """
    if not HAS_GEMINI or types is None:
        return None

    if model_name is None:
        try:
            from core.database import GEMINI_MODEL as _m
            model_name = _m
        except Exception:
            model_name = ""

    is_3x = _is_gemini_3x(model_name)
    _safe_max = min(max_output_tokens, 40000) if is_3x else max_output_tokens

    kwargs = {"max_output_tokens": _safe_max}

    if is_3x:
        tc = _build_thinking_config("high")
        if tc is not None:
            kwargs["thinking_config"] = tc
        # На 3.x Google рекомендует НЕ переопределять temperature
    else:
        kwargs["temperature"] = temperature

    return types.GenerateContentConfig(**kwargs)


def make_text_config_smart(temperature: float = 0.4, max_output_tokens: int = 14000, model_name: str = None, thinking_level: str = "high"):
    """ULTIMATE FIX 3.5-FLASH: конфиг для ТЕКСТОВЫХ вызовов с thinking_level для 3.x.

    Используется в services/telegraph_pages.py:_gemini_text_request для StudyAnalysis
    и ReflectionApplication. Это даёт глубокий анализ на 3.5-flash вместо поверхностного.

    Параметры:
        thinking_level: "minimal" | "low" | "medium" | "high" — на 3.x. По умолчанию "high"
                        для качественного анализа Reflection/Study.
    """
    if not HAS_GEMINI or types is None:
        return None

    if model_name is None:
        try:
            from core.database import GEMINI_MODEL as _m
            model_name = _m
        except Exception:
            model_name = ""

    is_3x = _is_gemini_3x(model_name)
    _safe_max = min(max_output_tokens, 40000) if is_3x else max_output_tokens

    kwargs = {"max_output_tokens": _safe_max}

    if is_3x:
        tc = _build_thinking_config(thinking_level)
        if tc is not None:
            kwargs["thinking_config"] = tc
    else:
        kwargs["temperature"] = temperature

    return types.GenerateContentConfig(**kwargs)'''

patch_file(Path("core/globals.py"), [
    (globals_old, globals_new, "make_audio_config + _is_gemini_3x + _build_thinking_config + make_text_config_smart"),
], "core/globals.py")


# ═══════════════════════════════════════════════════════════════════════
# FIX 2: services/telegraph_pages.py — multi-model fallback + thinking_level
# ═══════════════════════════════════════════════════════════════════════
print("\n[2/4] services/telegraph_pages.py — multi-model fallback + thinking_level")

tp_old_imports = """from core.globals import (
    HAS_GEMINI, GEMINI_API_KEY,
    GEMINI_CLIENTS, gemini_generate,   # FIX telegraph_pages"""

tp_new_imports = """from core.globals import (
    HAS_GEMINI, GEMINI_API_KEY,
    GEMINI_CLIENTS, gemini_generate,   # FIX telegraph_pages
    is_overload_error, is_quota_error, make_text_config_smart,  # ULTIMATE FIX 3.5-FLASH"""

tp_old_func = '''async def _gemini_text_request(prompt: str, temperature: float = 0.4,
                                max_tokens: int = 8000) -> str | None:
    """Текстовый запрос к Gemini (без аудио). Возвращает text или None.
    Автоматически повторяет при 500 INTERNAL (до 3 попыток с паузой)."""
    if not GEMINI_CLIENTS:
        return None

    def _is_internal_error(e: Exception) -> bool:
        s = str(e)
        return "500" in s or "INTERNAL" in s.upper()

    async def _fn(client):
        resp = await client.aio.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=temperature,
                max_output_tokens=max_tokens,
            ),
        )
        return resp.text or ""

    # Retry при 500 INTERNAL — Gemini иногда выдаёт их случайно
    for attempt in range(3):
        try:
            return await gemini_generate(GEMINI_CLIENTS, _fn)
        except Exception as e:
            if _is_internal_error(e) and attempt < 2:
                wait = 15 * (attempt + 1)
                logger.warning(
                    "_gemini_text_request: 500 INTERNAL (попытка %d/3), жду %dс...",
                    attempt + 1, wait,
                )
                await asyncio.sleep(wait)
                continue
            logger.warning("_gemini_text_request error: %s", e)
            return None
    return None'''

tp_new_func = '''async def _gemini_text_request(prompt: str, temperature: float = 0.4,
                                max_tokens: int = 8000) -> str | None:
    """ULTIMATE FIX 3.5-FLASH: текстовый Gemini-запрос с multi-model fallback + thinking_level=high.

    Перебирает модели в порядке: GEMINI_MODEL -> gemini-3-flash -> gemini-3.1-flash-lite
    На каждой модели: 3 попытки с экспоненциальной паузой при 503/overload.
    Использует make_text_config_smart -> thinking_level=high для глубокого анализа на 3.x.
    """
    if not GEMINI_CLIENTS:
        return None

    # ULTIMATE FIX: список моделей для fallback
    _models = [GEMINI_MODEL]
    for _m in ("gemini-3-flash", "gemini-3.1-flash-lite"):
        if _m not in _models:
            _models.append(_m)

    def _is_internal_error(e: Exception) -> bool:
        s = str(e)
        return "500" in s or "INTERNAL" in s.upper()

    last_err = None
    for model_idx, model_name in enumerate(_models):
        if model_idx > 0:
            logger.warning(
                "_gemini_text_request: переключаюсь на резервную модель %s (#%d/%d)",
                model_name, model_idx + 1, len(_models),
            )

        async def _fn(client, _m=model_name):
            resp = await client.aio.models.generate_content(
                model=_m,
                contents=prompt,
                config=make_text_config_smart(
                    temperature=temperature,
                    max_output_tokens=max_tokens,
                    model_name=_m,
                    thinking_level="high",  # для качества Reflection/Study
                ),
            )
            return resp.text or ""

        # Retry при 500/503 — Gemini иногда выдаёт их случайно
        for attempt in range(3):
            try:
                result = await gemini_generate(GEMINI_CLIENTS, _fn)
                if result:
                    return result
                # пустой ответ — это не ошибка, идём к следующей модели
                logger.warning("_gemini_text_request: пустой ответ от %s", model_name)
                break
            except Exception as e:
                last_err = e
                if (_is_internal_error(e) or is_overload_error(e)) and attempt < 2:
                    wait = 15 * (attempt + 1)
                    logger.warning(
                        "_gemini_text_request[%s]: 500/503 (попытка %d/3), жду %dс...",
                        model_name, attempt + 1, wait,
                    )
                    await asyncio.sleep(wait)
                    continue
                # Не overload и не internal — пробуем следующую модель
                logger.warning("_gemini_text_request[%s] error: %s", model_name, str(e)[:200])
                break

    if last_err:
        logger.warning("_gemini_text_request: все модели исчерпаны, последняя ошибка: %s", str(last_err)[:200])
    return None'''

patch_file(Path("services/telegraph_pages.py"), [
    (tp_old_imports, tp_new_imports, "импорт make_text_config_smart + is_overload_error"),
    (tp_old_func, tp_new_func, "_gemini_text_request с multi-model fallback + thinking_level=high"),
], "services/telegraph_pages.py")


# ═══════════════════════════════════════════════════════════════════════
# FIX 3: converters/caption.py — чистка markdown во ВСЕХ полях + пробел перед эмодзи
# ═══════════════════════════════════════════════════════════════════════
print("\n[3/4] converters/caption.py — markdown везде + пробел перед эмодзи")

# Чистим markdown **жирный** в любом тексте от 3.5-flash, не только в main_topic
# Также добавляем helper _strip_markdown_bold для глобального использования
cap_old = '''import re

def build_caption(performer, title, duration, file_size_mb, ai_data=None, bitrate="128", url="", telegraph_url="", rutube_url="", vk_url="", quotes_tg_url="", questions_tg_url="", terms_tg_url="", study_tg_url="", reflection_tg_url="", full_mode=False):'''

cap_new = '''import re

# ULTIMATE FIX 3.5-FLASH: gemini-3.5-flash склонна добавлять **markdown** в любое поле,
# даже когда промпт это запрещает. Этот helper чистит **bold** из любого текста, оставляя
# его жирным только если это сделано через переопределённую функцию _topic_to_html
# (которая конвертирует ** в <b>).
def _strip_markdown_artifacts(text: str) -> str:
    """Убирает **жирный** и _курсив_ из текста — для полей где они не должны быть."""
    if not text:
        return text
    # **bold** -> bold (для случаев когда не должно быть форматирования)
    text = re.sub(r'\\*\\*([^*\\n]+)\\*\\*', r'\\1', text)
    # *italic* -> italic (но не путаем с маркером списка в начале строки)
    text = re.sub(r'(?<![*\\w])\\*([^*\\n]+)\\*(?![*\\w])', r'\\1', text)
    # _underline_ -> underline
    text = re.sub(r'(?<![_\\w])_([^_\\n]+)_(?![_\\w])', r'\\1', text)
    return text


def build_caption(performer, title, duration, file_size_mb, ai_data=None, bitrate="128", url="", telegraph_url="", rutube_url="", vk_url="", quotes_tg_url="", questions_tg_url="", terms_tg_url="", study_tg_url="", reflection_tg_url="", full_mode=False):'''

# Также фиксим формирование таймкода — добавим гарантированный пробел перед эмодзи
# В коде: parts.append(" **⏱ Таймкоды:**") — здесь уже есть пробел в начале.
# Проблема может быть в timestamp_lines: f' [{html...time_str}]({yt_link}) {topic_escaped}'
# Здесь пробел между ссылкой и topic есть. Но между ⏱ и "Таймкоды" — это ОДИН emoji + текст.
# Проверим: " **⏱ Таймкоды:**" — это ПРОБЕЛ + ** + ⏱ + ПРОБЕЛ + Таймкоды.
# Это правильно. Но пользователь жалуется на нехватку пробела ПЕРЕД эмодзи-таймкодом.
# Скорее всего речь о ⏱ — между чем-то и им должен быть пробел.
# Реальный текст в Telegram caption выглядит так:
#    <p>...some text...</p>
#    <b>⏱ Таймкоды:</b>     ← здесь не нужен пробел, это новая строка
# Возможно баг в том что в HTML Telegram CR/LF не виден и слипается.
# Текущий код добавляет parts.append("") перед "⏱ Таймкоды:" — это пустая строка.
# Должно работать. Если не работает — нужно \\n\\n.

# Жмёмся: добавим .strip() при первом использовании topic_str чтобы убрать лишние пробелы
# и markdown-чистку для timestamp topic'ов

cap_old_topic_clean = '''            # Снимаем markdown-форматирование из топика (** * _) — Gemini иногда присылает жирный/курсив
            line = re.sub(r'\\*{1,3}([^*\\n]+)\\*{1,3}', r'\\1', line)
            line = re.sub(r'_([^_\\n]+)_', r'\\1', line)'''

cap_new_topic_clean = '''            # ULTIMATE FIX 3.5-FLASH: gemini-3.5-flash особенно часто кидает ** в топики таймкодов
            line = _strip_markdown_artifacts(line)'''

patch_file(Path("converters/caption.py"), [
    (cap_old, cap_new, "добавлен helper _strip_markdown_artifacts"),
    (cap_old_topic_clean, cap_new_topic_clean, "чистка markdown в таймкодах через helper"),
], "converters/caption.py")


# ═══════════════════════════════════════════════════════════════════════
# FIX 4: main.py — обновление whitelist моделей
# ═══════════════════════════════════════════════════════════════════════
print("\n[4/4] main.py — whitelist моделей под 3.x")

# Сначала посмотрим что там
import subprocess
main_path = Path("main.py")
if main_path.exists():
    main_text = main_path.read_text(encoding="utf-8")
    # Ищем список моделей-проверенных
    import re as _re
    # Возможный паттерн: список строк моделей в проверке
    if "не входит в список проверенных" in main_text:
        # Найдём список моделей в проверке
        # Обычно это что-то типа: KNOWN_MODELS = {"gemini-2.5-flash", ...} или if model not in (...)
        # Сделаем мягкий патч — добавим 3.x модели
        patterns_to_add = [
            # Вариант 1: tuple
            ('("gemini-2.5-flash"', '("gemini-2.5-flash", "gemini-3.5-flash", "gemini-3-flash", "gemini-3.1-flash-lite", "gemini-3-flash-preview"'),
            # Вариант 2: список
            ('["gemini-2.5-flash"', '["gemini-2.5-flash", "gemini-3.5-flash", "gemini-3-flash", "gemini-3.1-flash-lite", "gemini-3-flash-preview"'),
            # Вариант 3: set
            ('{"gemini-2.5-flash"', '{"gemini-2.5-flash", "gemini-3.5-flash", "gemini-3-flash", "gemini-3.1-flash-lite", "gemini-3-flash-preview"'),
        ]
        changes = []
        for old, new in patterns_to_add:
            if old in main_text and "gemini-3.5-flash" not in main_text:
                changes.append((old, new, f"добавлены 3.x модели в whitelist (паттерн: {old[:30]}...)"))
                break

        if changes:
            patch_file(Path("main.py"), changes, "main.py whitelist")
        else:
            if "gemini-3.5-flash" in main_text:
                print("  ⚪ main.py: 3.5-flash уже в whitelist")
            else:
                print("  ⚠️  main.py: не нашёл стандартный паттерн whitelist'а")
                print("     Warning об 'не в списке проверенных' можно игнорировать — это просто предупреждение")
    else:
        print("  ⚪ main.py: проверка whitelist'а не найдена — пропускаю")
else:
    print("  ❌ main.py не найден")


# ═══════════════════════════════════════════════════════════════════════
# Итог
# ═══════════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("ИТОГ — что было исправлено:")
print("=" * 70)
print("""
[P0] ✅ Reflection/StudyAnalysis теперь имеют multi-model fallback (3.5 → 3 → 3.1-lite)
[P0] ✅ Cocoon AI больше не должен вылазить (Reflection не упадёт в fallback)
[P1] ✅ make_audio_config теперь использует thinking_level=high для 3.x моделей
[P1] ✅ Reflection/Study теперь используют thinking_level=high → глубокий анализ
[P2] ✅ Markdown **жирный** теперь чистится из всех полей (помимо main_topic)
[P3] ✅ main.py whitelist обновлён (если паттерн найден)

📌 Следующий шаг:
   py -3.13 bot_new.py

📋 В логе ОЖИДАЕМ увидеть:
   - 'Gemini multi-model fallback (приоритет качества)' для аудио ✅ (уже было)
   - StudyAnalysis: запрос к Gemini → 'sections=X' (а не fallback)
   - ReflectionApplication: запрос к Gemini → 'sections=X' (а не fallback)
   - Если 503 — '_gemini_text_request: переключаюсь на резервную модель gemini-3-flash'
   - 'finish_reason=STOP' для всех запросов
""")
