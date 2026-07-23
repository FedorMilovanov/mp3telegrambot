"""Teacherly, material-led Study Analysis runtime.

The legacy Study prompt accumulated many useful safety rules, but its public-output
shape became a rubric: section types, cards, labels, and field-by-field answers.
This runtime keeps the safety boundaries while replacing the effective generation
prompt with a concise, material-led teaching brief.  Structured fields remain an
internal reliability aid; visible Telegraph prose must read as coherent Russian
paragraphs, never as a form filled in by the model.
"""
from __future__ import annotations

import re
from typing import Any, Callable

_MARKER = "TEACHERLY STUDY SYNTHESIS 2026-07-23"
_INSTALLED = False


TEACHERLY_STUDY_PROMPT = r"""
TEACHERLY STUDY SYNTHESIS 2026-07-23 — ГЛАВНОЕ ПРАВИЛО

Ты создаёшь страницу «Разбор материала» на основании реального конспекта и
стенограммы. Сделай глубокий, связный, обучающий текст, который можно читать как
хорошую богословскую главу. Начинай сразу с самой истины, различения или проблемы.
Не пиши вступлений вроде «в этом материале рассматривается», не описывай ролик со
стороны и не сообщай читателю, какие операции ты выполнил.

МАТЕРИАЛ
Название: {title}
Автор: {author}
Длительность: {duration}
Формат: {format_name}
Герменевтический метод: {hermeneutic_method}
Основная тема: {main_topic}
Краткий анализ: {analysis_summary}
Ход аргументации: {argument_arc}
Ключевые категории и таймкоды:
{key_categories}
{timestamps}

Черновые подсказки из первичного анализа; это не готовый план и не обязательный
словарь. Проверяй их по стенограмме и свободно отбрасывай слабые:
Понятия: {concepts}
Тексты Писания: {scripture}
Переводческие заметки: {translations}
Языковые заметки: {lexicon_notes}

ГЛАВНЫЙ ИСТОЧНИК
{synopsis_context}

РАЗРЕШЁННЫЙ ИСТОЧНИКОВЫЙ КОНТЕКСТ
{source_pack}

КОМПОЗИЦИЯ: УПРАВЛЯЕМАЯ СВОБОДА

Сам выбери композицию по реальному материалу. Обычно достаточно 3–7 сильных
разделов, но не заполняй норму. Заголовки должны рождаться из мысли материала, а
не повторять служебные категории «Ключевые понятия», «Ключевые тексты»,
«Лексика», «Источники». В сессии вопросов и ответов можно объединять родственные
ответы в тематические линии, но нельзя изображать единую систему там, где вопросы
действительно самостоятельны.

Каждый раздел пиши как один–три полноценных связанных абзаца. Абзац должен не
перечислять поля, а вести читателя: тезис → основание → важное различение →
следствие. Определение вводи внутри рассуждения именно там, где без него нельзя
двигаться дальше. Не создавай цепочки карточек «термин — определение — применение».
Списки допустимы лишь тогда, когда сам материал действительно содержит конечное
различение, последовательность или несколько параллельных видов; после списка
обязательно объясни связь и богословскую цену различий.

Используй **жирный** как смысловые опоры внутри прозы: ключевой тезис, контраст,
поворот аргумента, точное определение. Не жирни каждый термин и не превращай
абзац в россыпь ярлыков. Для содержательного раздела обычно нужны две–пять таких
опор, но выбирай их по смыслу, а не по счётчику.

ГЛУБИНА БЕЗ ИМИТАЦИИ НАУЧНОСТИ

Связывай идеи в целое только там, где связь действительно поддерживается речью,
Писанием или разрешённым источником. Можно вывести сильный синтез, но нельзя
придумывать красивую «архитектуру», если она сглаживает реальные различия между
ответами. Ясно отличай: что сказал автор; что утверждает текст Писания; что
принадлежит определённой богословской традиции; что является осторожным выводом
из нескольких оснований.

Спорное конфессиональное утверждение атрибутируй автору или традиции. Не выдавай
формулу конкретного реформатского, арминианского, лютеранского, православного или
диспенсационального чтения за нейтральное словарное значение. Внутренняя рамка
служит проверке точности и не должна появляться как «наша редакционная позиция».

Не выдумывай точных цитат, названий книг, страниц, исторических деталей и значений
слов. Прямая цитата допустима только при наличии её в стенограмме или надёжном
контексте. Если уверенности недостаточно, передай мысль без кавычек либо опусти.

ЯЗЫКИ ОРИГИНАЛА — ТОЛЬКО ОРГАНИЧНО

Греческое или еврейское слово включай лишь тогда, когда оно реально углубляет
чтение конкретного места Писания. Встрой наблюдение в обычный русский абзац: дай
читателю русскую фразу, форму слова в стихе и смысл именно в этом контексте, а
затем покажи, почему это важно для аргумента. Не выводи значение из разложения
слова на корни и не доказывай доктрину одним словом. Этимология, широкий
словарный диапазон и созвучие с русским словом сами по себе ничего не решают.
Если точный стих, форма, контекст или источник ненадёжны, языковую заметку опусти.
Не используй публичные метки «Базовое значение», «В этом стихе», «Роль в
аргументе», «Граница вывода», «Источник» как анкету: эти проверки выполняются
внутренне, а читатель получает цельную прозу.

ЗАБЛУЖДЕНИЯ И ОТВЕТ ОРТОДОКСИИ

Этот раздел создавай только при реальном основании в материале. Его заголовок
сохраняй дословно: «Заблуждения и ответ ортодоксии». Каждая проблема остаётся
парой двух отдельных абзацев:

**Название богословской проблемы** ❌ **Подмена: название заблуждения.**
Точное объяснение подмены и того, что она разрушает.

✅ **Ответ ортодоксальной церкви.**
Конкретный ответ Писания, Собора, Синода или исповедания; таймкод при наличии.

Не переноси эту пару в ReflectionApplication.

ТАЙМКОДЫ И ФОРМАТ

Таймкод ставь только при реальной связи с фрагментом, естественно внутри или в
конце предложения: ⏱ **M:SS** перед точкой. Внутри раздела таймкоды идут по
возрастанию и не могут быть раньше section.time. Заголовок section.time — время
начала соответствующей линии мысли, а не случайная ссылка.

Верни только валидный JSON без пояснений и code fence:
{{
  "outline": [{{"title": "содержательный русский заголовок", "time": "M:SS"}}],
  "sections": [
    {{
      "title": "тот же заголовок",
      "time": "M:SS",
      "content": "связные Markdown-абзацы с естественными **смысловыми опорами**"
    }}
  ]
}}

Не используй blocks по умолчанию: весь публичный материал пиши в content. Перед
ответом молча убери любой абзац, который можно без изменений вставить в другую
проповедь, который лишь повторяет конспект или имитирует глубину терминологией.
"""


_FIELD_LABEL_RE = re.compile(
    r"(?im)^\s*(?:\*\*)?(?:Русская фраза стиха|Базовое значение|В этом стихе|"
    r"Роль в аргументе(?: материала)?|Граница вывода|Источник)(?:\*\*)?\s*:"
)
_CARD_LINE_RE = re.compile(
    r"(?m)^\s*(?:[•\-]\s*)?\*\*[^*\n]{2,120}\*\*"
    r"(?:\s*\(\*\*[^*\n]{2,100}\*\*\))?\s*[—:]"
)
_BOLD_RE = re.compile(r"\*\*[^*\n]{2,180}\*\*")
_GENERIC_SECTION_RE = re.compile(
    r"^(?:ключевые понятия|ключевые тексты(?: и экзегетические узлы)?|"
    r"языки оригинала(?: и .*)?|ключевые слова в контексте писания|"
    r"переводческие развилки(?: и .*)?|источники|карта источников(?: и .*)?)$",
    re.IGNORECASE,
)


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _sentence(value: str) -> str:
    value = _clean(value)
    if not value:
        return ""
    return value if value.endswith((".", "!", "?", "…")) else value + "."


def render_word_study_as_prose(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Render an exact lexical observation as one coherent Russian paragraph.

    The schema may collect reliability fields, but the reader must not see a
    checklist.  Only the contextual core is mandatory; optional grammar,
    pronunciation, source, limit, and timestamp are woven in when present.
    """
    aliases = {
        "scripture_ref": ("scripture_ref", "ref"),
        "russian_quote": ("russian_quote", "quote"),
        "russian_focus": ("russian_focus", "focus"),
        "original_form": ("original_form",),
        "lemma": ("lemma",),
        "transliteration": ("transliteration",),
        "russian_pronunciation": ("russian_pronunciation", "pronunciation_ru"),
        "grammar": ("grammar",),
        "basic_meaning": ("basic_meaning", "dictionary_meaning"),
        "meaning_in_context": ("meaning_in_context", "contextual_meaning"),
        "role_in_argument": ("role_in_argument", "why_relevant"),
        "limits_of_claim": ("limits_of_claim",),
        "source": ("source", "source_label"),
        "anchor_timestamp": ("anchor_timestamp", "timestamp"),
    }

    def value(name: str) -> str:
        for key in aliases[name]:
            found = _clean(raw.get(key))
            if found:
                return found
        return ""

    values = {name: value(name) for name in aliases}
    required = (
        "scripture_ref",
        "russian_focus",
        "original_form",
        "lemma",
        "meaning_in_context",
        "role_in_argument",
    )
    if any(not values[name] for name in required):
        return None

    opening = f"**{values['scripture_ref']} — «{values['russian_focus']}».**"
    sentences: list[str] = [opening]
    if values["russian_quote"]:
        sentences.append(_sentence(f"Русский контекст: «{values['russian_quote']}»"))

    form = (
        f"В оригинале стоит **{values['original_form']}**, форма от *{values['lemma']}*"
    )
    reading_bits: list[str] = []
    if values["transliteration"]:
        reading_bits.append(f"*{values['transliteration']}*")
    if values["russian_pronunciation"]:
        reading_bits.append(f"примерно «{values['russian_pronunciation']}»")
    if reading_bits:
        form += " (" + ", ".join(reading_bits) + ")"
    if values["grammar"]:
        form += f"; грамматическая форма здесь — {values['grammar']}"
    sentences.append(_sentence(form))

    if values["basic_meaning"]:
        sentences.append(_sentence(f"Обычный смысл слова — {values['basic_meaning']}; здесь {values['meaning_in_context']}"))
    else:
        sentences.append(_sentence(f"В данном контексте {values['meaning_in_context']}"))
    sentences.append(_sentence(values["role_in_argument"]))
    if values["limits_of_claim"]:
        sentences.append(_sentence(values["limits_of_claim"]))

    tail: list[str] = []
    if values["source"]:
        tail.append(values["source"])
    if values["anchor_timestamp"]:
        tail.append(f"⏱ **{values['anchor_timestamp']}**")
    if tail:
        sentences.append(" (" + "; ".join(tail) + ").")

    text = " ".join(part.strip() for part in sentences if part.strip())
    text = re.sub(r"\s+([,.!?])", r"\1", text)
    return {"type": "paragraph", "text": text}


def _patch_word_study_renderer() -> None:
    from core import content_audit, structured_blocks
    from services import conspect_quality_contract

    current = structured_blocks.normalize_structured_block
    if getattr(current, "_teacherly_study_runtime", False):
        return

    def normalize_teacherly(raw: Any) -> dict[str, Any] | None:
        if isinstance(raw, dict):
            btype = _clean(raw.get("type")).lower()
            if btype in {"word_study", "wordstudy"}:
                return render_word_study_as_prose(raw)
        return current(raw)

    normalize_teacherly._teacherly_study_runtime = True  # type: ignore[attr-defined]
    structured_blocks.normalize_structured_block = normalize_teacherly
    content_audit.normalize_structured_block = normalize_teacherly
    conspect_quality_contract.normalize_word_study_block = render_word_study_as_prose


def _patch_teacherly_content_audit() -> None:
    from core import content_audit

    current = content_audit.audit_expanded_sections
    if getattr(current, "_teacherly_study_runtime", False):
        return

    def audit_teacherly(
        sections: list[dict],
        outline: list[dict] | None = None,
        *,
        label: str = "",
        expected_author: str = "",
    ):
        new_sections, new_outline, issues = current(
            sections,
            outline,
            label=label,
            expected_author=expected_author,
        )
        if label != "StudyAnalysis":
            return new_sections, new_outline, issues

        generic_titles = 0
        for idx, section in enumerate(new_sections):
            title = _clean(section.get("title"))
            content = str(section.get("content") or "")
            location = f"StudyAnalysis.sections[{idx}]"
            if _GENERIC_SECTION_RE.match(title):
                generic_titles += 1

            labels = len(_FIELD_LABEL_RE.findall(content))
            if labels >= 3:
                issues.append(content_audit.ContentAuditIssue(
                    code="study_checklist_prose_warning",
                    location=f"{location}.content",
                    message=(
                        "visible Study prose answers an internal field checklist; rewrite as "
                        "one coherent teacherly paragraph without field labels"
                    ),
                    before=content[:180],
                ))

            cards = len(_CARD_LINE_RE.findall(content))
            paragraphs = [p.strip() for p in re.split(r"\n\s*\n", content) if p.strip()]
            short_bold_cards = sum(
                1 for p in paragraphs
                if len(p) < 320 and re.match(r"^\s*(?:[•\-]\s*)?\*\*", p)
            )
            if cards >= 4 or (short_bold_cards >= 4 and short_bold_cards * 2 >= max(1, len(paragraphs))):
                issues.append(content_audit.ContentAuditIssue(
                    code="study_fragmented_cards_warning",
                    location=f"{location}.content",
                    message=(
                        "Study section is fragmented into definition cards; combine them into "
                        "connected explanatory paragraphs with an argument arc"
                    ),
                    before=content[:180],
                ))

            visible = re.sub(r"\s+", " ", content).strip()
            if len(visible) >= 700 and len(_BOLD_RE.findall(content)) < 2:
                issues.append(content_audit.ContentAuditIssue(
                    code="study_bold_anchor_missing_warning",
                    location=f"{location}.content",
                    message=(
                        "long Study section lacks semantic bold anchors; emphasize two or more "
                        "real theses or contrasts inside the prose"
                    ),
                    before=content[:180],
                ))

        if generic_titles >= 3:
            issues.append(content_audit.ContentAuditIssue(
                code="study_template_architecture_warning",
                location="StudyAnalysis.outline",
                message=(
                    "three or more generic rubric headings survived; choose material-specific "
                    "teaching headings and a natural composition"
                ),
            ))
        return new_sections, new_outline, issues

    audit_teacherly._teacherly_study_runtime = True  # type: ignore[attr-defined]
    content_audit.audit_expanded_sections = audit_teacherly
    content_audit._WARNING_CODES.update({
        "study_checklist_prose_warning",
        "study_fragmented_cards_warning",
        "study_bold_anchor_missing_warning",
        "study_template_architecture_warning",
    })


def install_teacherly_study_runtime() -> str:
    """Install the concise Study prompt and public-prose quality guards."""
    global _INSTALLED
    if _INSTALLED:
        return "teacherly Study synthesis already installed"

    from core import prompts

    # Synopsis remains untouched.  Only the effective Study prompt is replaced.
    prompts.STUDY_ANALYSIS_PROMPT = TEACHERLY_STUDY_PROMPT
    _patch_word_study_renderer()
    _patch_teacherly_content_audit()
    _INSTALLED = True
    return "material-led Study prose; no checklist/cards; organic lexical analysis"
