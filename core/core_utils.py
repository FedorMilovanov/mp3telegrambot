#!/usr/bin/env python3
"""
core_utils.py — Общие утилиты без зависимостей от других модулей проекта.

Сюда вынесены функции, которые раньше создавали циклические импорты:

  Цикл 1: json_parser → text_utils → json_parser
      Источник: time_to_seconds (была в json_parser.py)
      Решение:  text_utils теперь импортирует из core_utils напрямую

  Цикл 2: markdown → caption → markdown
      Источник: _fix_rtl_in_text (была в markdown.py),
                _md_parse_inline и helpers (были в caption.py)
      Решение:  оба модуля импортируют из core_utils, лазивых импортов нет

Правило: этот модуль не должен импортировать ничего из других модулей проекта.
"""
import re

# ─────────────────────────────────────────────────────────────────────────────
# Время / таймкоды
# ─────────────────────────────────────────────────────────────────────────────

def time_to_seconds(t: str) -> int | None:
    """Конвертирует M:SS или H:MM:SS в секунды.

    Возвращает None для невалидного/пустого входа, 0 для «0:00».
    """
    if not t or not t.strip():
        return None
    parts = t.strip().split(":")
    try:
        if len(parts) == 2:
            m, s = int(parts[0]), int(parts[1])
            if m < 0 or s < 0 or s >= 60:
                return None
            return m * 60 + s
        elif len(parts) == 3:
            h, m, s = int(parts[0]), int(parts[1]), int(parts[2])
            if h < 0 or m < 0 or s < 0 or m >= 60 or s >= 60:
                return None
            return h * 3600 + m * 60 + s
    except (ValueError, TypeError):
        pass
    return None


# ─────────────────────────────────────────────────────────────────────────────
# RTL / BiDi утилиты
# ─────────────────────────────────────────────────────────────────────────────

def _fix_rtl_in_text(text: str) -> str:
    """Чистит bidi-мусор от Gemini и обеспечивает корректный рендер RTL-слов
    (иврит, арабский) в Telegraph.

    PATCH V2: расширен с «только перед (» на «после любого RTL-слова»,
    чтобы браузер не переключал выравнивание всей строки вправо.
    """
    # Убираем bidi-изоляторы Gemini (НО не LTR/RTL Mark — убираем ниже отдельно)
    text = re.sub(r'[⁦-⁩‪-‮]', '', text)
    # Убираем старые наши LTR Mark чтобы не дублировать при повторном вызове
    text = text.replace('‎', '').replace('‏', '')

    _LTR = "‎"  # FIX: Python 3.13 запрещает \u в raw replacement string

    # LTR Mark + NBSP перед скобкой после RTL-слова
    text = re.sub(
        r'([֐-׿؀-ۿ][^\s,;)]*?)\s*\(',
        r'\1 ' + _LTR + '(',
        text,
    )
    # LTR Mark после RTL-слова перед пробелом/пунктуацией —
    # предотвращает «улёт» следующего символа вправо
    text = re.sub(
        r'([֐-׿؀-ۿ][^\s,;(.!?:]*?)([\s,;.!?:»"\)\]])',
        r'\1' + _LTR + r'\2',
        text,
    )
    return text

def _fix_unclosed_bold(text: str) -> str:
    """Страховка перед парсером: исправляет незакрытые ** в тексте.

    - Если ** висит в конце строки без пары — удаляем его.
    - Если ** открыт посередине строки без закрытия — закрываем в конце строки.
    - Корректный текст не трогаем.
    """
    lines = text.split('\n')
    result = []
    for line in lines:
        # Считаем **, не трогая ***
        tmp = line.replace('***', '\x00\x00\x00')
        count = tmp.count('**')
        if count % 2 == 0:
            result.append(line)
            continue
        stripped = line.rstrip()
        if stripped.endswith('**'):
            # Висящий ** в конце — убираем
            result.append(stripped[:-2].rstrip())
        else:
            # Открытый ** посередине — закрываем
            result.append(line + '**')
    return '\n'.join(result)


def _fix_inline_spacing(nodes: list) -> list:
    """Исправляет слипание жирного/курсивного текста с окружающим.

    PATCH V2: добавлен случай когда str идёт после dict(b/i) —
    раньше пробел не добавлялся и слова слипались.
    """
    if len(nodes) < 2:
        return nodes

    _PUNCT_START = (" ", "\n", ".", ",", ";", ":", "!", "?", ")", "\u00bb", "\u2014", "\u2013")
    _PUNCT_END   = (" ", "\n", "(", "\u00ab", "\u2014", "\u2013", "-", "/")

    result = []
    for i, node in enumerate(nodes):
        if isinstance(node, dict) and node.get("tag") in ("b", "i"):
            # Случай 1: предыдущий str — добавляем пробел в конец если нет
            if result and isinstance(result[-1], str):
                prev = result[-1]
                if prev and not prev.endswith(_PUNCT_END):
                    result[-1] = prev + " "
            # Случай 2: предыдущий dict(b/i) — вставляем пробел между ними
            elif result and isinstance(result[-1], dict) and result[-1].get("tag") in ("b", "i"):
                result.append(" ")
            result.append(node)
            # Случай 3: следующий str — добавляем пробел в начало если нет
            if i + 1 < len(nodes) and isinstance(nodes[i + 1], str):
                next_text = nodes[i + 1]
                if next_text and not next_text.startswith(_PUNCT_START):
                    nodes[i + 1] = " " + next_text
        elif isinstance(node, str):
            # Случай 4: предыдущий dict(b/i), текущий str без пробела — добавляем пробел
            if (result
                    and isinstance(result[-1], dict)
                    and result[-1].get("tag") in ("b", "i")
                    and node
                    and not node.startswith(_PUNCT_START)):
                result.append(" ")
            result.append(node)
        else:
            result.append(node)
    return result

def _md_parse_inline_inner(text: str) -> list:
    """Парсит только *курсив* и _курсив_ внутри уже жирного блока."""
    if re.search(r'[\u0590-\u05FF\u0600-\u06FF]', text):
        text = _fix_rtl_in_text(text)
    nodes = []
    # #49: убран re.DOTALL — не захватываем переносы строк
    pattern = re.compile(r'(\*([^\n]+?)\*|_([^\n]+?)_)')
    last = 0
    for m in pattern.finditer(text):
        if m.start() > last:
            nodes.append(text[last:m.start()])
        nodes.append({"tag": "i", "children": [m.group(2) or m.group(3)]})
        last = m.end()
    if last < len(text):
        nodes.append(text[last:])
    return nodes if nodes else [text]


def normalize_misbolded_bullet_lead(text: str) -> str:
    """Fix Gemini bullets where bold accidentally spans title + explanation.

    Examples:
      • **שָׁנַן — Показывает важность интенсивного**, глубокого...
      • **Directory for Family Worship. — Исторический документ**, содержащий...

    Desired:
      • **שָׁנַן** — Показывает важность интенсивного, глубокого...
      • **Directory for Family Worship** — Исторический документ, содержащий...
    """
    if not text or "**" not in text:
        return text

    def repl(m: re.Match) -> str:
        bullet = m.group("bullet")
        title = m.group("title").strip().rstrip(".")
        desc = m.group("desc").strip()
        return f"{bullet}**{title}** — {desc}"

    return re.sub(
        r"(?m)^(?P<bullet>\s*[•\-]\s*)\*\*"
        r"(?P<title>[^*\n—–-]{2,120}?)\s*[—–-]\s*"
        r"(?P<desc>[^*\n]{8,240}?)\*\*",
        repl,
        text,
    )


def _polish_timestamps_in_text(text: str) -> str:
    """FINAL-POLISH FIX 2-4: фиксы реальных багов Gemini в inline-таймкодах.

    Применять к каждой строке текста ДО _md_parse_inline:

    FIX 2: слипание текст-эмодзи     'надежды⏱ 19:50' → 'надежды ⏱ 19:50'
    FIX 3: точка после таймкода       'Богом ⏱ 15:55.' → 'Богом. ⏱ 15:55'
    FIX 4: слипание точка-bold       'тлении.**В материале:**' → 'тлении. **В материале:**'
    FIX 4b: слипание bold-bold       '**А****B**' → '**A** **B**'
    """
    if not text:
        return text

    # FIX 4: пробел между .!?:; и **bold-лидером**
    # Не трогаем уже корректные: '. **' остаётся '. **'
    text = re.sub(r'([\.\!\?\:\;])(\*\*[^\*\n])', r'\1 \2', text)

    # FIX 4b: пробел между ** и ** если идут вплотную (закрывающий + открывающий)
    text = re.sub(r'(\*\*)(\*\*[^\*\n])', r'\1 \2', text)

    # FIX 2: пробел перед эмодзи-таймкодом если его нет
    # Маркеры таймкодов: ⏱, 📌, 🎯, 🔔
    text = re.sub(r'([а-яА-ЯёЁa-zA-Z0-9\)\]\»\"])([⏱📌🎯🔔])', r'\1 \2', text)
    # FIX 2026-05-24: пробел между закрывающим ** и ⏱
    text = text.replace('**⏱', '** ⏱')
    text = text.replace('**\u00a0⏱', '** \u00a0⏱')

    # FIX 3 (v10 REWRITE): таймкод должен стоять ПЕРЕД завершающей пунктуацией.
    # Правило: «текст ⏱ 49:00.» — ПРАВИЛЬНО (TS часть предложения, точка после него).
    #          «текст. ⏱ 49:00 Следующее.» — ПЛОХО (TS "висит" между двух предложений).
    #
    # Старый FIX 3 делал наоборот: переставлял точку ПЕРЕД TS → нарушал правило.
    # Новое поведение:
    #   «текст ⏱ 49:00.»                → без изменений (уже правильно)
    #   «текст. ⏱ 49:00 Следующее.»     → без изменений (сложный случай, не трогаем)
    #
    # ЕДИНСТВЕННОЕ что чиним: если текст обрывается «⏱ 49:00\n» без пунктуации,
    # и ⏱ единственный элемент в конце строки — оставляем как есть.
    # (убран агрессивный regex который ломал правильные случаи)

    return text


def _md_parse_inline(text: str) -> list:
    """**bold**, *italic*, ***bold+italic***, _italic_ → Telegraph nodes.

    Поддерживает вложенный курсив внутри жирного: **текст *курсив* текст**
    """
    # Gemini иногда экранирует Markdown как \*\*...\*\*. Telegraph тогда
    # показывает сырые ** в тексте. Возвращаем только форматные маркеры,
    # не трогая произвольные backslash-последовательности.
    text = (str(text or "")
            .replace(r"\*\*\*", "***")
            .replace(r"\*\*", "**")
            .replace(r"\*", "*")
            .replace(r"\_", "_"))
    text = _fix_unclosed_bold(text)
    nodes = []
    # Быстрая защита от malformed markdown с избыточными * — предотвращает ReDoS
    if text.count('*') > 200 or text.count('_') > 100:
        return [text.replace('**', '').replace('*', '').replace('_', '')]
    # Bold/italic не должны захватывать двойной перенос строки (граница абзаца).
    pattern = re.compile(
        r'(\*\*\*((?:(?!\n\n).)+?)\*\*\*|\*\*((?:(?!\n\n).)+?)\*\*|\*((?:(?!\n\n).)+?)\*|_((?:(?!\n\n).)+?)_)'
    )
    last = 0
    for m in pattern.finditer(text):
        if m.start() > last:
            nodes.append(text[last:m.start()])

        def _make_b_node(children):
            return {"tag": "b", "children": children}

        if m.group(2):
            nodes.append(_make_b_node([{"tag": "i", "children": [m.group(2)]}]))
        elif m.group(3):
            # Жирный блок — рекурсивно парсим содержимое на вложенный курсив
            inner_nodes = _md_parse_inline_inner(m.group(3))
            nodes.append(_make_b_node(inner_nodes))
        else:
            nodes.append({"tag": "i", "children": [m.group(4) or m.group(5)]})
        last = m.end()
    if last < len(text):
        nodes.append(text[last:])
    # Fallback: убираем сырые ** которые не распознал парсер
    nodes = [n.replace('**', '') if isinstance(n, str) else n for n in nodes]
    return _fix_inline_spacing(nodes) if nodes else [text]
