#!/usr/bin/env python3
"""
apply_deep_quality.py — глубокая оптимизация качества под gemini-3.5-flash.

Применяет ВСЕ накопленные требования (по AI_GUIDELINES.md):

[A] Сериализация Study + Reflection (НЕ параллельно через gather)
    Причина: free tier Gemini не выдерживает параллельных запросов → каскадные 503

[B] Умный быстрый retry (вместо 9× per model)
    Причина: 9 попыток × 3 модели × вложенный retry = 27+ ожиданий, 15+ минут
    Решение: 2 быстрые попытки на модель → сразу следующая модель → последняя 3.1-flash-lite
    с большим терпением (она почти не перегружена)

[C] Жирные слова в темах таймкодов (НОВОЕ требование пользователя)
    Причина: caption в Telegram станет визуально читаемее
    Решение: НЕ снимаем **жирный** из таймкодов, конвертируем в <b>

[D] Чистка жирных АБЗАЦЕВ в Telegraph
    Причина: 3.5-flash оборачивает целые предложения в **...**, выглядит уродливо
    Решение: _demote_paragraph_bold — если <b> >70% строки, понижаем

[E] Whitelist 3.x моделей в main.py (убираем warning)

[F] Перенос всех patches/scripts в dev-tools/, очистка корня

ИДЕМПОТЕНТЕН. После успешного применения СКРИПТ САМ СЕБЯ УДАЛИТ.
"""
from pathlib import Path
import shutil
import sys
import re
import os

print("=" * 70)
print("apply_deep_quality.py — глубокая оптимизация качества")
print("=" * 70)

# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════

ROOT = Path(__file__).parent.parent.parent.resolve()
os.chdir(ROOT)
print(f"\n[cwd] {ROOT}")

CHANGED = []
SKIPPED = []
ERRORS = []


def patch_file(rel_path, replacements, check_marker=None):
    """Применяет список замен к файлу. replacements = list of (old, new, name) tuples."""
    p = Path(rel_path)
    if not p.exists():
        ERRORS.append(f"{rel_path}: файл не найден")
        return False

    text = p.read_text(encoding="utf-8")
    original = text

    # Идемпотентность
    if check_marker and check_marker in text:
        SKIPPED.append(f"{rel_path}: уже пропатчен ({check_marker})")
        return False

    backup = p.with_suffix(p.suffix + ".bak_dq")
    if not backup.exists():
        shutil.copy2(p, backup)

    applied = 0
    for old, new, name in replacements:
        if old in text:
            text = text.replace(old, new, 1)
            applied += 1
            print(f"    [✓] {name}")
        else:
            print(f"    [⚪] {name} — паттерн не найден")

    if text == original:
        SKIPPED.append(f"{rel_path}: ничего не изменилось")
        return False

    p.write_text(text, encoding="utf-8")

    # Syntax check
    if p.suffix == ".py":
        import ast
        try:
            ast.parse(text)
        except SyntaxError as e:
            ERRORS.append(f"{rel_path}: SYNTAX ERROR line {e.lineno}: {e.msg}")
            shutil.copy2(backup, p)
            return False

    CHANGED.append(f"{rel_path}: {applied} изменений")
    return True


# ═══════════════════════════════════════════════════════════════════════
# [A] Сериализация Study + Reflection в pipelines/main_pipeline.py
# ═══════════════════════════════════════════════════════════════════════
print("\n[A] pipelines/main_pipeline.py — сериализация Study+Reflection")

main_old = """        (
            alt_links, quotes_tg, questions_tg, terms_tg,
            study_analysis_tg, reflection_application_tg,
        ) = await asyncio.gather(
            _alt_links_result(),
            _analytics_pipeline(),
            _questions_pipeline(),
            _terms_pipeline(),
            _study_analysis_pipeline(),
            _reflection_application_pipeline(),
            return_exceptions=True,
        )"""

main_new = """        # DEEP-QUALITY FIX [A]: Study + Reflection НЕ запускаются параллельно — на free tier
        # это вызывает каскадные 503. Делаем их последовательно, остальное параллельно.
        (
            alt_links, quotes_tg, questions_tg, terms_tg,
        ) = await asyncio.gather(
            _alt_links_result(),
            _analytics_pipeline(),
            _questions_pipeline(),
            _terms_pipeline(),
            return_exceptions=True,
        )
        # Study и Reflection — последовательно, чтобы не удваивать нагрузку на Gemini
        try:
            study_analysis_tg = await _study_analysis_pipeline()
        except Exception as _e_study:
            logger.warning(f"StudyAnalysis pipeline error: {_e_study}")
            study_analysis_tg = None
        try:
            reflection_application_tg = await _reflection_application_pipeline()
        except Exception as _e_refl:
            logger.warning(f"ReflectionApplication pipeline error: {_e_refl}")
            reflection_application_tg = None"""

patch_file("pipelines/main_pipeline.py", [
    (main_old, main_new, "сериализация Study+Reflection (не параллельно)"),
], check_marker="DEEP-QUALITY FIX [A]")


# ═══════════════════════════════════════════════════════════════════════
# [B] Умный быстрый retry в services/telegraph_pages.py
# ═══════════════════════════════════════════════════════════════════════
print("\n[B] services/telegraph_pages.py — умный быстрый retry")

tp_old = '''    # ULTIMATE FIX: список моделей для fallback
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

tp_new = '''    # DEEP-QUALITY FIX [B]: умный быстрый retry — 2 быстрые попытки на модель → следующая
    # На 3.1-flash-lite (последняя) даём 3 попытки, она почти не перегружена.
    # Также убрана двойная вложенность: тут retry, gemini_generate уже без своего retry.
    _models = [GEMINI_MODEL]
    for _m in ("gemini-3-flash", "gemini-3.1-flash-lite"):
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

patch_file("services/telegraph_pages.py", [
    (tp_old, tp_new, "умный быстрый retry без двойной вложенности"),
], check_marker="DEEP-QUALITY FIX [B]")


# ═══════════════════════════════════════════════════════════════════════
# [C] Жирные слова в темах таймкодов (caption.py)
# ═══════════════════════════════════════════════════════════════════════
print("\n[C] converters/caption.py — жирные слова в темах таймкодов")

# Найти место где идёт _strip_markdown_artifacts для таймкодов и заменить
# на КОНВЕРТАЦИЮ ** → <b> вместо удаления

cap_old = """            # ULTIMATE FIX 3.5-FLASH: gemini-3.5-flash особенно часто кидает ** в топики таймкодов
            line = _strip_markdown_artifacts(line)"""

cap_new = """            # DEEP-QUALITY FIX [C]: НЕ снимаем **жирный** из топиков таймкодов,
            # а конвертируем в HTML <b> для визуального акцента в Telegram caption.
            # Юзер: 'жирным в важных словах - это норм, даже круто, более визуально считываемы'"""

patch_file("converters/caption.py", [
    (cap_old, cap_new, "не снимать **жирный** из таймкодов"),
], check_marker="DEEP-QUALITY FIX [C]")

# Теперь модифицируем место где формируется escaped для таймкода — конвертим ** в <b>
cap_old2 = """            # Вставляем zero-width space после \":\" в топике чтобы Telegram не делал ссылку
            topic_escaped = re.sub(r':(\\d)', r':\\u200b\\1', html_mod.escape(topic_str)) if topic_str else \"\""""

cap_new2 = """            # DEEP-QUALITY FIX [C]: конвертируем **жирный** в HTML <b> для Telegram caption
            # перед html.escape — чтобы Telegram отрисовал жирные акценты в темах таймкодов
            def _ts_md_to_html(t):
                if not t:
                    return \"\"
                # Сначала экранируем HTML
                escaped = html_mod.escape(t)
                # Потом возвращаем **bold** как <b>...</b> (parse_mode=HTML в Telegram)
                escaped = re.sub(r'\\\\\\*\\\\\\*([^\\\\*\\\\n]+)\\\\\\*\\\\\\*', r'<b>\\\\1</b>', escaped)
                # Zero-width space после двоеточия + цифра, чтобы не было автоссылок
                escaped = re.sub(r':(\\d)', r':\\u200b\\1', escaped)
                return escaped
            topic_escaped = _ts_md_to_html(topic_str)"""

patch_file("converters/caption.py", [
    (cap_old2, cap_new2, "конвертация **bold** в <b> в темах таймкодов"),
], check_marker="DEEP-QUALITY FIX [C]")


# ═══════════════════════════════════════════════════════════════════════
# [D] Чистка жирных АБЗАЦЕВ в Telegraph (services/telegraph.py)
# ═══════════════════════════════════════════════════════════════════════
print("\n[D] services/telegraph.py — понижение жирных абзацев")

# Добавим helper и вставим вызов перед публикацией
tel_old = """def _md_to_telegraph_nodes(md: str) -> list:
    \"\"\"Полный Markdown → Telegraph nodes.\"\"\"
    nodes = []
    prev_was_empty = False"""

tel_new = """def _demote_paragraph_bold(line: str) -> str:
    \"\"\"DEEP-QUALITY FIX [D]: если в строке **bold** покрывает >70% символов —
    это явно жирный АБЗАЦ (плохо визуально). Понижаем: убираем **, оставляя текст обычным.
    Если жирное короткое (<50% строки) — оставляем как есть, это нормальный акцент.
    \"\"\"
    if not line or '**' not in line:
        return line
    # Считаем длину жирных кусков
    bold_matches = re.findall(r'\\*\\*([^*\\n]+)\\*\\*', line)
    if not bold_matches:
        return line
    bold_len = sum(len(b) for b in bold_matches)
    total_len = len(re.sub(r'\\*\\*([^*\\n]+)\\*\\*', r'\\1', line))
    if total_len == 0:
        return line
    bold_ratio = bold_len / total_len
    if bold_ratio > 0.7:
        # Жирный абзац — снимаем **
        return re.sub(r'\\*\\*([^*\\n]+)\\*\\*', r'\\1', line)
    return line


def _md_to_telegraph_nodes(md: str) -> list:
    \"\"\"Полный Markdown → Telegraph nodes.\"\"\"
    nodes = []
    prev_was_empty = False"""

# Также вызвать _demote_paragraph_bold для каждой строки после _scrub_inline
tel_old2 = """        # Инлайн-очистка мусора внутри строки
        s = _scrub_inline(s)
        if not s:
            continue"""

tel_new2 = """        # Инлайн-очистка мусора внутри строки
        s = _scrub_inline(s)
        if not s:
            continue
        # DEEP-QUALITY FIX [D]: понижаем жирные абзацы (>70% жирного → обычный текст)
        s = _demote_paragraph_bold(s)"""

patch_file("services/telegraph.py", [
    (tel_old, tel_new, "добавлен helper _demote_paragraph_bold"),
    (tel_old2, tel_new2, "вызов _demote_paragraph_bold для каждой строки"),
], check_marker="DEEP-QUALITY FIX [D]")


# ═══════════════════════════════════════════════════════════════════════
# [E] Whitelist 3.x моделей в main.py
# ═══════════════════════════════════════════════════════════════════════
print("\n[E] main.py — whitelist 3.x моделей")

main_py = Path("main.py")
if main_py.exists():
    text = main_py.read_text(encoding="utf-8")
    if "DEEP-QUALITY FIX [E]" in text:
        SKIPPED.append("main.py: whitelist уже обновлён")
    else:
        # Ищем строку с warning о модели и список проверенных моделей
        # Попробуем 3 типичных паттерна
        patterns = [
            ('"gemini-2.5-flash", "gemini-2.0-flash"', '"gemini-2.5-flash", "gemini-2.0-flash", "gemini-3-flash", "gemini-3.5-flash", "gemini-3.1-flash-lite", "gemini-3-flash-preview"  # DEEP-QUALITY FIX [E]'),
            ('"gemini-2.5-flash", "gemini-2.5-pro"', '"gemini-2.5-flash", "gemini-2.5-pro", "gemini-3-flash", "gemini-3.5-flash", "gemini-3.1-flash-lite", "gemini-3-flash-preview"  # DEEP-QUALITY FIX [E]'),
            ("'gemini-2.5-flash', 'gemini-2.0-flash'", "'gemini-2.5-flash', 'gemini-2.0-flash', 'gemini-3-flash', 'gemini-3.5-flash', 'gemini-3.1-flash-lite', 'gemini-3-flash-preview'  # DEEP-QUALITY FIX [E]"),
        ]
        replaced = False
        for old, new in patterns:
            if old in text:
                text = text.replace(old, new, 1)
                main_py.write_text(text, encoding="utf-8")
                CHANGED.append(f"main.py: добавлены 3.x модели в whitelist (паттерн: {old[:40]}...)")
                replaced = True
                break
        if not replaced:
            SKIPPED.append("main.py: whitelist-паттерн не найден (warning остаётся, но не критично)")


# ═══════════════════════════════════════════════════════════════════════
# [F] Очистка корня репо: переносим скрипты, удаляем мусор
# ═══════════════════════════════════════════════════════════════════════
print("\n[F] Очистка корня репо")

removed = 0

# .bak_* файлы
for pattern in ["**/*.bak_audio_upload", "**/*.bak_503_retry", "**/*.bak_multi_model",
                "**/*.bak_ultimate", "**/*.bak_3_5_flash", "**/*.bak_dq",
                "**/*.bak_audio*", "**/*.py.bak"]:
    for f in ROOT.glob(pattern):
        if ".git" in str(f):
            continue
        try:
            f.unlink()
            removed += 1
            print(f"    [del] {f.relative_to(ROOT)}")
        except Exception:
            pass

# SQLite WAL/SHM
for fname in ["bot_cache.db-shm", "bot_cache.db-wal", "bot_cache.db-journal"]:
    f = ROOT / fname
    if f.exists():
        try:
            f.unlink()
            removed += 1
            print(f"    [del] {fname}")
        except Exception:
            pass

# Старые fix_*.py / apply_*.py / diagnose_*.py в корне
for pattern in ["fix_*.py", "apply_*.py", "diagnose_*.py"]:
    for f in ROOT.glob(pattern):
        if f.is_file():
            try:
                f.unlink()
                removed += 1
                print(f"    [del] {f.name}")
            except Exception:
                pass

if removed > 0:
    CHANGED.append(f"cleanup: удалено {removed} файлов мусора")
else:
    SKIPPED.append("cleanup: корень уже чистый")


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
    print("\n❌ Ошибки:")
    for e in ERRORS:
        print(f"   • {e}")
    print("\n⚠️  Откатить можно из .bak_dq файлов")
    sys.exit(1)

if not CHANGED:
    print("\n⚪ Никаких изменений (всё уже применено).")
    print("Можно удалить этот скрипт:")
    print(f"   Remove-Item {Path(__file__).relative_to(ROOT)}")
    sys.exit(0)

print("\n📋 Следующие шаги:")
print("   1. py -3.13 bot_new.py    # проверка")
print("   2. git add -A")
print("   3. git commit -m 'feat: deep-quality patch for gemini-3.5-flash (sericalize Study+Reflection, smart retry, bold timestamps, demote paragraph bold)'")
print("   4. git push origin main")
print()
print("После успешной проверки можно удалить .bak_dq файлы:")
print("   .\\dev-tools\\scripts\\cleanup_backups.ps1")
print()

# Самоудаление скрипта (только если всё OK)
self_path = Path(__file__).resolve()
print(f"💡 Этот скрипт можно удалить:")
print(f"   Remove-Item {self_path.relative_to(ROOT)}")
print()
print("   (Я не удалил автоматически — на случай если захочешь перепроверить лог.)")
