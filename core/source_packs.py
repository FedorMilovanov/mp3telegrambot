#!/usr/bin/env python3
"""
source_packs.py — Duration-aware, scored source packs for Study Analysis.

Instead of handing Gemini a giant bibliography or selecting one flat keyword
match, we choose a small, deduplicated, topic-weighted allowlist. The goal is to
help Gemini reason with better sources, not to constrain it with more bans.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

_PACKS: dict[str, list[str]] = {
    "justification": [
        "1689 LBCF гл.11", "Westminster Confession гл.11",
        "Sproul — Faith Alone", "Calvin — Institutes III.11–18",
        "Owen — Justification by Faith", "Murray — Redemption Accomplished",
        "Luther — Galatians Commentary",
    ],
    "repentance": [
        "1689 LBCF гл.15", "Heidelberg Catechism Q88–90",
        "Watson — The Doctrine of Repentance",
        "Edwards — Religious Affections", "Owen — Mortification of Sin",
        "Brooks — Precious Remedies Against Satan's Devices",
    ],
    "scripture_authority": [
        "1689 LBCF гл.1", "Chicago Statement on Biblical Inerrancy (1978)",
        "Warfield — Inspiration and Authority of the Bible",
        "Calvin — Institutes I.6–9", "Frame — Systematic Theology (Scripture)",
    ],
    "scripture_sufficiency": [
        "MacArthur — The Sufficiency of Scripture", "1689 LBCF гл.1",
        "Chicago Statement on Biblical Inerrancy (1978)",
        "Warfield — Inspiration and Authority of the Bible",
        "Packer — Fundamentalism and the Word of God", "Frame — The Doctrine of the Word of God",
    ],
    "soteriology": [
        "1689 LBCF гл.10–11", "Canons of Dort (1619)",
        "Calvin — Institutes III", "Owen — The Death of Death in the Death of Christ",
        "Murray — Redemption Accomplished and Applied",
        "Sproul — Chosen by God",
    ],
    "sanctification": [
        "1689 LBCF гл.13", "Westminster Confession гл.13",
        "Owen — Of the Mortification of Sin in Believers",
        "Bridges — The Pursuit of Holiness",
        "Ryle — Holiness", "Ferguson — Devoted to God",
    ],
    "christology": [
        "Chalcedonian Definition (451)", "Athanasius — On the Incarnation",
        "Calvin — Institutes II.12–17", "Owen — Christologia",
        "Warfield — The Person and Work of Christ", "Macleod — The Person of Christ",
    ],
    "isaiah_servant": [
        "Calvin — Commentary on Isaiah", "Motyer — The Prophecy of Isaiah",
        "Young — The Book of Isaiah", "Oswalt — The Book of Isaiah",
        "Owen — The Death of Death in the Death of Christ", "Murray — Redemption Accomplished and Applied",
        "Grogan — Isaiah",
    ],
    "trinity": [
        "Nicene Creed (325/381)", "Athanasius — On the Incarnation",
        "Augustine — De Trinitate", "Calvin — Institutes I.13",
        "Bavinck — Reformed Dogmatics (God and Creation)", "Letham — The Holy Trinity",
    ],
    "pneumatology": [
        "Owen — The Holy Spirit", "Calvin — Institutes III.1–2",
        "Gaffin — Perspectives on Pentecost", "Ferguson — The Holy Spirit",
        "Bavinck — Reformed Dogmatics (Holy Spirit, Church, New Creation)",
    ],
    "eschatology": [
        "Hoekema — The Bible and the Future", "Ryrie — Basic Theology (Things to Come)",
        "Berkhof — Systematic Theology (Eschatology)", "Ladd — The Blessed Hope",
        "Chou — The Hermeneutics of the Biblical Writers",
    ],
    "angelology": [
        "Calvin — Institutes I.14", "Bavinck — Reformed Dogmatics (Creation)",
        "Berkhof — Systematic Theology (Angels)", "Warfield — Biblical Doctrines",
        "MacArthur — The Invisible Army",
    ],
    "ecclesiology": [
        "1689 LBCF гл.26–29", "Dever — Nine Marks of a Healthy Church",
        "Clowney — The Church", "Horton — The Christian Faith (Church)",
        "MacArthur — The Master's Plan for the Church",
    ],
    "preaching_hermeneutics": [
        "MacArthur — Preaching: How to Preach Biblically",
        "Chapell — Christ-Centered Preaching",
        "Goldsworthy — Preaching the Whole Bible as Christian Scripture",
        "Greidanus — Preaching Christ from the Old Testament",
        "Lloyd-Jones — Preaching and Preachers",
        "Piper — The Supremacy of God in Preaching",
    ],
    "charismatic_cessationism": [
        "Warfield — Counterfeit Miracles",
        "MacArthur — Strange Fire / Чуждый огонь",
        "Gaffin — Perspectives on Pentecost",
        "1689 LBCF гл.1 (достаточность Писания)",
        "Masters — The Charismatic Phenomenon",
    ],
    "covenant_theology": [
        "1689 LBCF гл.7", "Westminster Confession гл.7",
        "Witsius — The Economy of the Covenants",
        "Berkhof — Systematic Theology (Covenant)",
        "Robertson — The Christ of the Covenants",
        "Chou — The Hermeneutics of the Biblical Writers",
    ],
    "israel_church": [
        "Chou — The Hermeneutics of the Biblical Writers",
        "MacArthur — The Gospel According to the Apostles",
        "Kaiser — Toward Rediscovering the Old Testament",
        "Hoekema — The Bible and the Future",
        "Ryrie — Dispensationalism",
    ],
    "infant_salvation_children": [
        "Spurgeon — Infant Salvation", "MacArthur — Safe in the Arms of God",
        "1689 LBCF гл.10 пар.3", "Canons of Dort I.17",
        "Watson — A Body of Divinity",
    ],
    "evangelism_gospel": [
        "MacArthur — The Gospel According to Jesus",
        "Washer — The Gospel's Power and Message",
        "Packer — Evangelism and the Sovereignty of God",
        "Murray — Redemption Accomplished and Applied",
        "Fuller — The Gospel Worthy of All Acceptation",
        "1689 LBCF гл.20 (Евангелие)",
    ],
    "mission": [
        "DeYoung and Gilbert — What Is the Mission of the Church?",
        "Piper — Let the Nations Be Glad!", "Fuller — The Gospel Worthy of All Acceptation",
        "Packer — Evangelism and the Sovereignty of God", "Bavinck — An Introduction to the Science of Missions",
    ],
    "prayer_spirituality": [
        "Calvin — Institutes III.20", "Owen — Communion with God",
        "Bunyan — Pilgrim's Progress", "Carson — A Call to Spiritual Reformation",
        "Whitney — Spiritual Disciplines for the Christian Life",
    ],
    "providence_suffering": [
        "Calvin — Institutes I.16–18", "Watson — All Things for Good",
        "Burroughs — The Rare Jewel of Christian Contentment",
        "Piper — Suffering and the Sovereignty of God", "Westminster Confession гл.5",
    ],
    "social_justice_gospel": [
        "Dallas Statement on Social Justice and the Gospel (2018)",
        "Baucham — Fault Lines", "Machen — Christianity and Liberalism",
        "DeYoung and Gilbert — What Is the Mission of the Church?",
        "MacArthur — The Gospel According to Jesus",
    ],
    "church_history_biography": [
        "Spurgeon — Lectures to My Students", "MacArthur — Ashamed of the Gospel",
        "Fuller — The Gospel Worthy of All Acceptation", "Dallimore — Spurgeon",
        "Haykin — The Christian Lover",
    ],
    "default": [
        "1689 London Baptist Confession", "Westminster Confession of Faith",
        "Calvin — Institutes of the Christian Religion",
        "Owen — Works (тематически релевантный том)",
        "Spurgeon — Sermons", "MacArthur Study Bible",
    ],
}

# Backward-compatible keyword map for tests and simple external uses.
_KEYWORD_TO_TOPIC: dict[str, str] = {
    "оправдан": "justification", "justif": "justification", "sola fide": "justification",
    "покаян": "repentance", "repent": "repentance", "умерщвлен": "repentance",
    "авторитет писани": "scripture_authority", "непогрешим": "scripture_authority", "inerrancy": "scripture_authority",
    "достаточность писания": "scripture_sufficiency", "sufficiency": "scripture_sufficiency",
    "избрание": "soteriology", "монергизм": "soteriology", "суверенная благодать": "soteriology",
    "освящен": "sanctification", "святость": "sanctification", "holiness": "sanctification",
    "христолог": "christology", "мессия": "christology", "incarnation": "christology",
    "исайя 53": "isaiah_servant", "раб иеговы": "isaiah_servant", "страдающий раб": "isaiah_servant",
    "троиц": "trinity", "trinity": "trinity", "святой дух": "pneumatology", "pneumatology": "pneumatology",
    "эсхатолог": "eschatology", "eschatology": "eschatology", "великая скорб": "eschatology",
    "ангел": "angelology", "angel": "angelology", "ангелолог": "angelology",
    "экклесиолог": "ecclesiology", "поместная церковь": "ecclesiology",
    "проповед": "preaching_hermeneutics", "hermeneutic": "preaching_hermeneutics", "кафедра": "preaching_hermeneutics",
    "харизмат": "charismatic_cessationism", "цессационизм": "charismatic_cessationism", "чуждый огонь": "charismatic_cessationism",
    "завет": "covenant_theology", "covenant": "covenant_theology", "израиль": "israel_church",
    # AUDIT R40: «дети» (общее) убрано — пак про ВЕЧНУЮ УЧАСТЬ УМЕРШИХ младенцев,
    # а не про детей вообще; проповедь «Христос и дети» (Лк 18) получала книги
    # про смерть младенцев. Остаётся специфичный «младенец».
    "младенец": "infant_salvation_children",
    "евангелизм": "evangelism_gospel", "евангелие": "evangelism_gospel", "благовести": "evangelism_gospel",
    "миссия": "mission", "великое поручение": "mission", "mission": "mission",
    "молитва": "prayer_spirituality", "prayer": "prayer_spirituality",
    "провидение": "providence_suffering", "страдан": "providence_suffering",
    "социальная справедливость": "social_justice_gospel", "social justice": "social_justice_gospel",
    "сперджен": "church_history_biography", "история церкви": "church_history_biography",
}


@dataclass(frozen=True)
class TopicRule:
    positive: tuple[tuple[str, float], ...]
    negative: tuple[str, ...] = ()


_TOPIC_RULES: dict[str, TopicRule] = {
    topic: TopicRule(tuple((kw, 1.0) for kw, mapped in _KEYWORD_TO_TOPIC.items() if mapped == topic))
    for topic in _PACKS
    if topic != "default"
}
# Higher precision overrides. Keep "церковь" out of generic ecclesiology: it is
# too common in prayer/mission/sermon materials and caused false source packs.
_TOPIC_RULES["ecclesiology"] = TopicRule(
    positive=(("экклесиолог", 3.0), ("поместная церковь", 3.0), ("членство", 2.0), ("церковная дисциплина", 2.5), ("невеста христа", 2.0)),
    negative=(("молитва",), ("ангел",), ("исайя 53",)),
)
_TOPIC_RULES["prayer_spirituality"] = TopicRule(
    positive=(("молитва", 3.0), ("молиться", 3.0), ("отче наш", 3.0), ("духовная дисциплина", 2.0), ("prayer", 2.0)),
    negative=(("социальная справедливость",), ("ангел",)),
)
_TOPIC_RULES["isaiah_servant"] = TopicRule(
    positive=(("исайя 53", 4.0), ("исаия 53", 4.0), ("раб иеговы", 4.0), ("страдающий раб", 3.5), ("муж скорбей", 3.0), ("заместительное искупление", 2.5)),
    negative=(("ангел",), ("харизмат",)),
)
_TOPIC_RULES["scripture_sufficiency"] = TopicRule(
    positive=(("достаточность писания", 4.0), ("достаточность слова", 3.0), ("псалом 18", 3.0), ("психолог", 2.0), ("авторитет библии", 2.0)),
    negative=(("миссия",), ("ангел",)),
)
_TOPIC_RULES["angelology"] = TopicRule(
    positive=(("ангел", 3.0), ("ангелолог", 4.0), ("небесное воинство", 3.0), ("невидимая армия", 3.0), ("angel", 2.0)),
    negative=(("исайя 53",), ("раб иеговы",)),
)
_TOPIC_RULES["mission"] = TopicRule(
    positive=(("миссия церкви", 4.0), ("великое поручение", 4.0), ("делать учеников", 3.0), ("благовестие", 2.0), ("mission", 2.0)),
    negative=(("молитва",), ("ангел",)),
)


def _normalize(text: str) -> str:
    text = (text or "").lower().replace("ё", "е")
    text = re.sub(r"[\[\]{}()<>\"'«».,!?;:|]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _score_topics(search_text: str) -> dict[str, float]:
    text = _normalize(search_text)
    scores: dict[str, float] = {}
    for topic, rule in _TOPIC_RULES.items():
        score = 0.0
        for kw, weight in rule.positive:
            # AUDIT R40: совпадение по ГРАНИЦЕ СЛОВА, а не подстрокой — иначе
            # «ангел» цеплял «евАНГЕЛие»/«евангелизм», и евангелизационная
            # проповедь получала пак по АНГЕЛОЛОГИИ (а правильный evangelism
            # подавлялся). \b перед началом ключа сохраняет матч словоформ
            # («ангелы», «благовестие»).
            if re.search(r"\b" + re.escape(_normalize(kw)), text):
                score += float(weight)
        for neg in rule.negative:
            # Support accidental tuple entries from older declarations.
            neg_text = neg[0] if isinstance(neg, tuple) else neg
            if re.search(r"\b" + re.escape(_normalize(str(neg_text))), text):
                score -= 2.0
        if score > 0:
            scores[topic] = score
    return scores


def _dedupe_sources(sources: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for src in sources:
        key = _normalize(re.sub(r"\s*/\s*[^—]+$", "", src))
        key = re.sub(r"\b(гл|chapter|chapters?)\.?\s*\d+[–\-\d.]*", "", key)
        if key in seen:
            continue
        seen.add(key)
        out.append(src)
    return out


def _max_sources_for_duration(duration_seconds: int | float = 0) -> int:
    try:
        dur = int(duration_seconds or 0)
    except (TypeError, ValueError):
        dur = 0
    if dur and dur < 20 * 60:
        return 4
    if dur and dur >= 60 * 60:
        return 7
    return 5


def select_source_pack_topics(key_categories: list[str], main_topic: str = "", max_topics: int = 2) -> list[tuple[str, float]]:
    """Return scored topics for diagnostics/tests."""
    search_text = " ".join([str(x) for x in (key_categories or [])] + [main_topic or ""])
    scores = _score_topics(search_text)
    if not scores:
        return [("default", 0.0)]
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    # Keep a secondary topic only if it is meaningful, not a weak incidental hit.
    primary_score = ranked[0][1]
    selected = [ranked[0]]
    selected.extend(
        item for item in ranked[1:]
        if item[1] >= max(2.0, primary_score * 0.55)
    )
    return selected[:max(1, max_topics)]


def get_source_pack(key_categories: list[str], main_topic: str = "", duration_seconds: int | float = 0,
                    max_sources: int | None = None) -> str:
    """Return a scored, deduplicated source allowlist for Study Analysis."""
    selected = select_source_pack_topics(key_categories, main_topic)
    limit = int(max_sources or _max_sources_for_duration(duration_seconds))
    merged: list[str] = []
    for topic, _score in selected:
        merged.extend(_PACKS.get(topic, _PACKS["default"]))
    sources = _dedupe_sources(merged)[:limit]
    topics_display = ", ".join(t for t, _ in selected)
    lines = [
        f"Приоритетные источники для данного материала (темы: {topics_display}; максимум {limit}):",
        "Используй эти источники как ориентир. Не добавляй случайные книги ради украшения.",
        "Названия на английском не переводи, кроме устойчивых официальных русских названий.",
        "Не смешивай традиции: реформаторы/пуритане не являются диспенсационалистами; Chou/Ryrie уместны только там, где реально обсуждаются Израиль, Церковь, пророчества или герменевтика.",
    ]
    for s in sources:
        lines.append(f"- {s}")
    return "\n".join(lines)


def get_source_pack_for_ai_data(ai_data: dict) -> str:
    """Convenience wrapper for telegraph_pages.py."""
    key_categories = (ai_data or {}).get("key_categories") or []
    main_topic = (ai_data or {}).get("main_topic") or ""
    duration = (ai_data or {}).get("duration") or 0
    return get_source_pack(key_categories, main_topic, duration_seconds=duration)
