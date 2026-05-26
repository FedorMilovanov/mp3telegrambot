#!/usr/bin/env python3
"""
source_packs.py — Тематические пакеты источников для Study Analysis.

Вместо подачи Gemini полного списка 100+ авторов всегда,
выбираем релевантный пакет по ключевым категориям материала.

Используется в services/telegraph_pages.py перед вызовом Gemini.
"""
from __future__ import annotations

_PACKS: dict[str, list[str]] = {
    "justification": [
        "1689 LBCF гл.11", "Westminster Confession гл.11",
        "Sproul — Faith Alone", "Calvin Institutes III.11–18",
        "Owen — Justification by Faith", "Murray — Redemption Accomplished",
        "Luther — Галатам (комментарий)",
    ],
    "repentance": [
        "1689 LBCF гл.15", "Heidelberg Catechism Q88–90",
        "Watson — The Doctrine of Repentance",
        "Edwards — Religious Affections", "Owen — Mortification of Sin",
        "Brooks — Precious Remedies",
    ],
    "scripture_authority": [
        "1689 LBCF гл.1", "Chicago Statement on Biblical Inerrancy (1978)",
        "Warfield — Inspiration and Authority of the Bible",
        "Calvin Institutes I.6–9", "Frame — Systematic Theology (Scripture)",
    ],
    "soteriology": [
        "1689 LBCF гл.10–11", "Canons of Dort (1619)",
        "Calvin Institutes III", "Owen — The Death of Death",
        "Murray — Redemption Accomplished and Applied",
        "Sproul — Chosen by God",
    ],
    "sanctification": [
        "1689 LBCF гл.13", "Westminster Confession гл.13",
        "Owen — Mortification of Sin",
        "Bridges — The Pursuit of Holiness",
        "Ryle — Holiness", "Ferguson — Devoted to God",
    ],
    "ecclesiology": [
        "1689 LBCF гл.26–29", "Dever — Nine Marks of a Healthy Church",
        "Clowney — The Church", "Horton — The Christian Faith (Church)",
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
        "MacArthur — Strange Fire",
        "Gaffin — Perspectives on Pentecost",
        "1689 LBCF гл.1 (достаточность Писания)",
        "Masters — Charismatic Phenomenon",
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
        "Kaiser — Toward Rediscovering the OT",
        "Hoekema — The Bible and the Future",
        "Berkhof — Systematic Theology (эсхатология)",
    ],
    "infant_salvation_children": [
        "Spurgeon — Infant Salvation (проповедь)",
        "MacArthur — Safe in the Arms of God",
        "1689 LBCF гл.10 пар.3",
        "Canons of Dort гл.1 ст.17",
        "Watson — A Body of Divinity",
    ],
    "evangelism_gospel": [
        "MacArthur — The Gospel According to Jesus",
        "Washer — The Gospel's Power and Message",
        "Packer — Evangelism and the Sovereignty of God",
        "Murray — Redemption Accomplished and Applied",
        "1689 LBCF гл.20 (Евангелие)",
    ],
    "prayer_spirituality": [
        "Calvin Institutes III.20",
        "Owen — Communion with God",
        "Bunyan — Pilgrim's Progress",
        "Carson — A Call to Spiritual Reformation",
        "Whitney — Spiritual Disciplines for the Christian Life",
    ],
    "providence_suffering": [
        "Calvin Institutes I.16–18",
        "Watson — All Things for Good",
        "Burroughs — The Rare Jewel of Christian Contentment",
        "Piper — Suffering and the Sovereignty of God",
        "Westminster Confession гл.5",
    ],
    "default": [
        "1689 London Baptist Confession",
        "Westminster Confession of Faith",
        "Calvin — Institutes of the Christian Religion",
        "Owen — Works (тематически релевантный том)",
        "Spurgeon — Sermons",
        "MacArthur Study Bible",
    ],
}

_KEYWORD_TO_TOPIC: dict[str, str] = {
    "оправдан": "justification", "justif": "justification",
    "sola fide": "justification", "только верой": "justification",
    "покаян": "repentance", "repent": "repentance",
    "умерщвлен": "repentance", "mortif": "repentance",
    "авторитет писани": "scripture_authority",
    "непогрешим": "scripture_authority", "inerrancy": "scripture_authority",
    "возрожден": "soteriology", "regenerat": "soteriology",
    "избрание": "soteriology", "election": "soteriology",
    "монергизм": "soteriology", "monergism": "soteriology",
    "суверенная благодать": "soteriology", "sovereign grace": "soteriology",
    "тулип": "soteriology", "tulip": "soteriology",
    "освящен": "sanctification", "sanctif": "sanctification",
    "святость": "sanctification", "holiness": "sanctification",
    "экклесиолог": "ecclesiology", "поместная": "ecclesiology",
    "церковь": "ecclesiology",
    "проповед": "preaching_hermeneutics",
    "hermeneutic": "preaching_hermeneutics",
    "экспозиционн": "preaching_hermeneutics",
    "expository": "preaching_hermeneutics",
    "подготовк": "preaching_hermeneutics",
    "кафедра": "preaching_hermeneutics",
    "харизмат": "charismatic_cessationism",
    "charismatic": "charismatic_cessationism",
    "цессационизм": "charismatic_cessationism",
    "чуждый огонь": "charismatic_cessationism",
    "пятидесятник": "charismatic_cessationism",
    "завет": "covenant_theology", "covenant": "covenant_theology",
    "израиль": "israel_church", "диспенсационализм": "israel_church",
    "младенец": "infant_salvation_children",
    "дети": "infant_salvation_children",
    "children": "infant_salvation_children",
    "возраст ответственности": "infant_salvation_children",
    "евангелизм": "evangelism_gospel", "евангелие": "evangelism_gospel",
    "evangelism": "evangelism_gospel", "gospel": "evangelism_gospel",
    "благовести": "evangelism_gospel",
    "молитва": "prayer_spirituality", "prayer": "prayer_spirituality",
    "провидение": "providence_suffering",
    "страдан": "providence_suffering", "suffering": "providence_suffering",
}


def get_source_pack(key_categories: list[str], main_topic: str = "") -> str:
    """Возвращает тематический пакет источников по ключевым категориям."""
    search_text = " ".join(key_categories + [main_topic or ""]).lower()
    topic_scores: dict[str, int] = {}
    for keyword, topic in _KEYWORD_TO_TOPIC.items():
        if keyword.lower() in search_text:
            topic_scores[topic] = topic_scores.get(topic, 0) + 1
    if topic_scores:
        best_topic = max(topic_scores, key=lambda t: topic_scores[t])
        sources = _PACKS.get(best_topic, _PACKS["default"])
    else:
        sources = _PACKS["default"]
    lines = ["Приоритетные источники для данного материала:"]
    for s in sources:
        lines.append(f"- {s}")
    return "\n".join(lines)


def get_source_pack_for_ai_data(ai_data: dict) -> str:
    """Удобная обёртка для вызова из telegraph_pages.py."""
    key_categories = (ai_data or {}).get("key_categories") or []
    main_topic = (ai_data or {}).get("main_topic") or ""
    return get_source_pack(key_categories, main_topic)
