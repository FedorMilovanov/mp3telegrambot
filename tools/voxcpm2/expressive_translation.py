#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Rhetoric-preserving Russian translation for expressive spoken dubbing.

This is a preparation-stage editor, not a TTS wrapper. It keeps factual and
theological fidelity while preserving the discourse devices that make a sermon
sound alive: repetition, direct address, questions, contrast, escalation,
climax, concrete imagery, cadence and natural breathing points.
"""
from __future__ import annotations

import json
import re
from typing import Any

from tools.voxcpm2 import generic_short_production as pipeline
from tools.voxcpm2 import strict_translation_payload

POLICY = "expressive-spoken-translation-v2"
_MAX_WORDS_PER_SECOND = 3.05
_PROGRESS_PREFIX = "DUB_PROGRESS "


def _payload(groups: list[dict[str, Any]]) -> str:
    return json.dumps(groups, ensure_ascii=False, indent=2)


def _validate(value: Any, groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return strict_translation_payload.validate_full(value, groups)


def _gemini(prompt: str, model_name: str) -> Any:
    return pipeline.gemini_json(prompt, model_name=model_name)


def _progress(progress: int, stage: str, message: str) -> None:
    payload = {
        "progress": max(0, min(int(progress), 94)),
        "stage": str(stage)[:160],
        "message": str(message)[:500],
    }
    pipeline.log(_PROGRESS_PREFIX + json.dumps(payload, ensure_ascii=False))
    pipeline.log(message)


def translate_groups(
    groups: list[dict[str, Any]],
    *,
    metadata: dict[str, Any] | None = None,
    caption_origin: str = "",
    model_name: str,
) -> list[dict[str, Any]]:
    """Three editorial passes, then duration-aware compression only where needed."""
    source_json = _payload(groups)
    context = {
        "video_title": str((metadata or {}).get("title") or ""),
        "channel": str(
            (metadata or {}).get("uploader")
            or (metadata or {}).get("channel")
            or ""
        ),
        "source_language": str(
            (metadata or {}).get("language")
            or (metadata or {}).get("original_language")
            or "unknown"
        ),
        "caption_origin": str(caption_origin or ""),
    }
    draft_prompt = f"""
Ты — первоклассный переводчик живой исходной речи для профессионального русского дубляжа.

Переведи весь фрагмент с исходного языка на русский как ЕДИНУЮ развивающуюся устную речь, но верни каждый исходный ID отдельно.

Обязательные правила точности:
1. Сохрани каждое утверждение, отрицание, условие, причину, вывод, число, имя, библейскую ссылку и богословский термин.
2. Ничего не добавляй, не объясняй, не толкуй и не усиливай сверх оригинала.
3. Не смягчай резкость автора и не превращай её в крикливость.
4. Не подменяй конкретное образное действие абстрактным пересказом. Глаголы насмешки, движения, удара, плача и другие физически ощутимые действия должны оставаться риторически конкретными, если именно это говорит автор.
5. Отличай цитату Писания от следующего авторского комментария. Узнаваемый русский перевод стиха допустим, но комментарий проповедника нельзя сглаживать под стиль библейского перевода.
6. В богословской терминологии исправляй только реальную смысловую подмену. Близкий нормативный русский эквивалент не объявляй ошибкой лишь ради буквального совпадения словаря; при возможности выбирай формулировку, точнее передающую мысль в данном контексте.

Обязательные правила живой речи:
7. Сохрани намеренные повторы, анафоры, параллельные конструкции, прямое обращение, риторические вопросы, контрасты и нарастание к кульминации. Не заменяй их сухим пересказом.
8. Перестрой порядок слов исходного языка в естественную разговорно-ораторскую русскую фразу.
9. Пунктуацией обозначай реальные смысловые паузы, повороты и ударения. Не дроби речь запятыми механически и не добавляй ремарок в скобках.
10. Каждая реплика должна естественно продолжать предыдущую и подготавливать следующую; не начинай каждый ID как новый дикторский абзац.
11. Сохрани ID один к одному. Не объединяй и не дроби блоки.
12. Учитывай start/end: текст должен произноситься без скороговорки, но сокращать смысл запрещено.

Верни только JSON:
{{"segments":[{{"id":1,"russian":"..."}}]}}

КОНТЕКСТ РОЛИКА:
{json.dumps(context, ensure_ascii=False)}

ИСХОДНАЯ РЕЧЬ:
{source_json}
""".strip()
    _progress(14, "Gemini: перевод 1/3", "Gemini: начинаю черновой перевод 1/3.")
    draft = _validate(_gemini(draft_prompt, model_name), groups)
    _progress(24, "Gemini: перевод 1/3 готов", "Gemini: черновой перевод 1/3 готов.")

    fidelity_prompt = f"""
Ты — старший двуязычный редактор богословского дубляжа. Построчно сверь русский черновик с исходной речью, одновременно читая соседние ID как единый контекст.

Исправляй только реальные ошибки:
- пропущенная или добавленная мысль;
- неверное отрицание, причинность, условие, число, имя, ссылка или термин;
- ослабленный либо чрезмерно усиленный тон;
- конкретный глагол, образ или прямое обращение, превращённые в отвлечённый пересказ;
- перепутанная граница между цитатой Писания и комментарием автора;
- калька и неестественный русский синтаксис;
- потерянный намеренный повтор, вопрос, контраст, обращение или ступень риторического нарастания;
- фраза, которая звучит как письменный перевод, а не живая речь.

Не создавай замечание из допустимой контекстной синонимии. Не украшай текст от себя. Не удаляй смысловые повторы ради краткости. Сохрани ID один к одному.
Верни только JSON:
{{"segments":[{{"id":1,"russian":"..."}}]}}

КОНТЕКСТ:
{json.dumps(context, ensure_ascii=False)}

ОРИГИНАЛ:
{source_json}

ЧЕРНОВИК:
{json.dumps(draft, ensure_ascii=False, indent=2)}
""".strip()
    _progress(27, "Gemini: сверка 2/3", "Gemini: начинаю смысловую сверку 2/3.")
    faithful = _validate(_gemini(fidelity_prompt, model_name), groups)
    _progress(37, "Gemini: сверка 2/3 готова", "Gemini: смысловая сверка 2/3 готова.")

    performance_prompt = f"""
Ты — режиссёр русской речевой записи и финальный литературный редактор. Сделай последнюю правку текста для живого мужского голоса, не меняя фактов и смысла.

Проверяй весь фрагмент как одно выступление:
1. Реплики должны соединяться в непрерывную мысль, а не звучать как независимые карточки.
2. Оставь сильные повторы, вопросы, обращения, контрасты, конкретные действия и кульминацию там, где они есть в оригинале.
3. Не превращай вызов, насмешку, предупреждение или исповедание в нейтральное описание настроения. Фраза должна оставлять актёру тот же речевой импульс, что и оригинал.
4. Расставь естественные смысловые паузы и удобные места дыхания средствами обычной русской пунктуации.
5. Убери канцелярит, книжную кальку, лишние местоимения и искусственные вводные слова.
6. Не добавляй эмоциональных прилагательных, ремарок, междометий и указаний актёру.
7. Не превращай речь в телеграфные обрубки и не делай все предложения одинаковой длины.
8. Сохрани ID один к одному и каждую исходную мысль.

Верни только JSON:
{{"segments":[{{"id":1,"russian":"..."}}]}}

ОРИГИНАЛ:
{source_json}

ПРОВЕРЕННЫЙ ПЕРЕВОД:
{json.dumps(faithful, ensure_ascii=False, indent=2)}
""".strip()
    _progress(40, "Gemini: редактура 3/3", "Gemini: начинаю речевую редактуру 3/3.")
    final = _validate(_gemini(performance_prompt, model_name), groups)
    _progress(50, "Gemini: редактура 3/3 готова", "Gemini: речевая редактура 3/3 готова.")

    overloaded: list[dict[str, Any]] = []
    for source, translated in zip(groups, final, strict=True):
        seconds = max(1.0, float(source["end"]) - float(source["start"]))
        rate = len(
            re.findall(r"\w+", translated["russian"], flags=re.UNICODE)
        ) / seconds
        if rate > _MAX_WORDS_PER_SECOND:
            overloaded.append(
                {
                    "id": int(source["id"]),
                    "seconds": round(seconds, 3),
                    "source": str(
                        source.get("source") or source.get("english") or ""
                    ),
                    "russian": translated["russian"],
                    "words_per_second": round(rate, 3),
                }
            )

    if overloaded:
        compression_prompt = f"""
Ты — редактор произносимости. Сократи ТОЛЬКО перегруженные русские реплики до естественного темпа не выше примерно {_MAX_WORDS_PER_SECOND:.2f} слова в секунду.

Запрещено удалять:
- любое утверждение, отрицание, условие, причину или вывод;
- имя, число, ссылку и термин;
- конкретное образное действие и прямое обращение;
- намеренный риторический повтор;
- вопрос, контраст или кульминационный поворот.

Убирай только кальку, дублирующее служебное слово, тяжёлый оборот или русскую словесную избыточность. Не превращай фразу в конспект, нейтральный пересказ или телеграфный стиль.
Верни только JSON для перечисленных ID:
{{"segments":[{{"id":1,"russian":"..."}}]}}

ПЕРЕГРУЖЕННЫЕ РЕПЛИКИ:
{json.dumps(overloaded, ensure_ascii=False, indent=2)}
""".strip()
        _progress(
            52,
            "Gemini: произносимость",
            f"Gemini: сокращаю {len(overloaded)} перегруженных реплик без потери смысла.",
        )
        compact = strict_translation_payload.validate_subset(
            _gemini(compression_prompt, model_name),
            [item["id"] for item in overloaded],
        )
        compact_by_id = {
            int(item["id"]): str(item["russian"])
            for item in compact
        }
        for item in final:
            if int(item["id"]) in compact_by_id:
                item["russian"] = compact_by_id[int(item["id"])]
        _progress(56, "Gemini: произносимость готова", "Gemini: перегруженные реплики сокращены.")
    else:
        _progress(54, "Gemini: перевод готов", "Gemini: все три редакторских прохода завершены; сжатие не требуется.")

    return final


__all__ = ["POLICY", "translate_groups"]
