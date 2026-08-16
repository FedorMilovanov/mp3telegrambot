#!/usr/bin/env python3
"""Temporary branch-only refactor runner for the zero-runtime-surgery marathon."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def write(rel: str, text: str) -> None:
    (ROOT / rel).write_text(text, encoding="utf-8")


def replace_once(rel: str, old: str, new: str) -> None:
    text = read(rel)
    if old not in text:
        raise RuntimeError(f"missing anchor in {rel}: {old[:100]!r}")
    write(rel, text.replace(old, new, 1))
    print(f"patched {rel}")


def remove_manifest_feature(text: str, feature_id: str) -> str:
    pattern = re.compile(
        r'\n    RuntimeFeature\(\n        [\"\']' + re.escape(feature_id) + r'[\"\'],.*?\n    \),',
        re.DOTALL,
    )
    text2, count = pattern.subn("", text, count=1)
    if count != 1:
        raise RuntimeError(f"manifest feature missing: {feature_id}")
    return text2


def main() -> int:
    policy = '''#!/usr/bin/env python3\n"""Canonical media-title casing and public filename policy.\n\nPure functions only: callers own when and where the policy is applied.\n"""\nfrom __future__ import annotations\n\nimport re\nfrom pathlib import Path\nfrom typing import Any\n\nfrom core.person_names import normalize_person_names\n\nRU_SERVICE_WORDS = frozenset({\n    "а", "без", "бы", "в", "во", "да", "для", "до", "же", "за", "и",\n    "из", "или", "к", "ко", "ли", "между", "на", "над", "не", "ни", "но",\n    "о", "об", "от", "по", "под", "при", "про", "с", "со", "у", "через",\n})\n\n_PRESERVE_CASE = {\n    "esv": "ESV", "kjv": "KJV", "nasb": "NASB", "niv": "NIV",\n    "lsb": "LSB", "nlt": "NLT", "csb": "CSB", "nkjv": "NKJV",\n    "rsv": "RSV", "net": "NET", "nrsv": "NRSV", "leb": "LEB",\n    "asv": "ASV", "lbcf": "LBCF", "lbcf1689": "LBCF1689",\n    "wcf": "WCF", "tulip": "TULIP", "q&a": "Q&A", "qa": "QA",\n    "youtube": "YouTube", "rutube": "RuTube", "vk": "VK",\n    "iphone": "iPhone", "ipad": "iPad", "na28": "NA28",\n    "bhs": "BHS", "lxx": "LXX",\n}\n\n_EDGE_RE = re.compile(r"^([^А-Яа-яЁёA-Za-z0-9]*)(.*?)([^А-Яа-яЁёA-Za-z0-9]*)$")\n_CYRILLIC_RE = re.compile(r"[А-Яа-яЁё]")\n_SPACED_DASH_RE = re.compile(r"\\s+([—–-])\\s+")\n_DELIVERY_MARKERS = tuple(sorted({\n    " — русский дубляж", " — только русский голос", " — русские субтитры",\n    " — исходные субтитры", " — точная расшифровка", " — шаблон перевода",\n    " — проверка перевода", " — перевод",\n}, key=len, reverse=True))\n\n\ndef _split_edges(token: str) -> tuple[str, str, str]:\n    match = _EDGE_RE.match(token)\n    return match.groups() if match else ("", token, "")\n\n\ndef _capitalize(word: str) -> str:\n    for index, char in enumerate(word):\n        if char.isalpha():\n            return word[:index] + char.upper() + word[index + 1:]\n    return word\n\n\ndef canonical_media_title(value: Any) -> str:\n    """Return project Title Case while preserving punctuation and proper case."""\n    text = re.sub(r"\\s+", " ", str(value or "")).strip(" .—–-")\n    text = _SPACED_DASH_RE.sub(lambda match: f" {match.group(1)} ", text)\n    if not text or not _CYRILLIC_RE.search(text):\n        return text\n    result: list[str] = []\n    for index, raw in enumerate(text.split()):\n        prefix, core, suffix = _split_edges(raw)\n        if not core:\n            result.append(raw)\n            continue\n        folded = core.casefold()\n        if index > 0 and folded in RU_SERVICE_WORDS:\n            normalized = core.lower()\n        elif folded in _PRESERVE_CASE:\n            normalized = _PRESERVE_CASE[folded]\n        elif re.search(r"[а-яё][А-ЯЁ]", core) or re.search(r"-[А-ЯЁ]", core):\n            normalized = core\n        elif len(core) >= 2 and core.isupper() and core.isalpha():\n            normalized = core\n        else:\n            normalized = _capitalize(core.lower())\n        result.append(prefix + normalized + suffix)\n    return normalize_person_names(" ".join(result))\n\n\ndef canonical_delivery_filename(value: Any) -> str:\n    """Canonicalize only the title portion of a user-facing filename."""\n    filename = Path(str(value or "")).name\n    if not filename:\n        return filename\n    suffix = Path(filename).suffix\n    stem = filename[:-len(suffix)] if suffix else filename\n    if not _CYRILLIC_RE.search(stem):\n        return filename\n    folded = stem.casefold()\n    for marker in _DELIVERY_MARKERS:\n        position = folded.find(marker)\n        if position > 0:\n            return canonical_media_title(stem[:position]) + stem[position:] + suffix\n    return canonical_media_title(stem) + suffix\n\n\ndef media_title_policy_contract() -> tuple[bool, str]:\n    ok = (\n        canonical_media_title("Сила И Достоинство Благочестивой Женщины - Джон Пайпер")\n        == "Сила и Достоинство Благочестивой Женщины - Джон Пайпер"\n        and canonical_delivery_filename(\n            "Сила И Достоинство - Джон Пайпер — русский дубляж.mp4"\n        ) == "Сила и Достоинство - Джон Пайпер — русский дубляж.mp4"\n    )\n    return ok, "source-owned canonical Russian Title Case + delivery filename policy"\n\n\n__all__ = [\n    "RU_SERVICE_WORDS", "canonical_delivery_filename", "canonical_media_title",\n    "media_title_policy_contract",\n]\n'''
    write("core/media_title_policy.py", policy)

    # core.text_utils owns ordinary behavior and directly delegates aggressive media titles.
    rel = "core/text_utils.py"
    text = read(rel)
    anchor = '''    if not s or not _is_cyrillic_dominant(s):\n        return s\n\n    def split_edges'''
    replacement = '''    if not s or not _is_cyrillic_dominant(s):\n        return s\n    if aggressive_title_case:\n        from core.media_title_policy import canonical_media_title\n        return canonical_media_title(s)\n\n    def split_edges'''
    if anchor not in text:
        raise RuntimeError("text_utils title anchor missing")
    write(rel, text.replace(anchor, replacement, 1))

    # DubStore normalizes historical/project rows at its own DB boundary.
    rel = "services/dub_studio.py"
    text = read(rel)
    import_anchor = "from __future__ import annotations\n"
    if import_anchor not in text:
        raise RuntimeError("DubStore future-import anchor missing")
    text = text.replace(import_anchor, import_anchor + "\nfrom core.media_title_policy import canonical_media_title\n", 1)
    row_anchor = '''        item["metadata"] = _json_load(item.pop("metadata_json", "{}"), {})\n        item["progress"] = int(item.get("progress") or 0)\n        return item\n'''
    row_new = '''        item["metadata"] = _json_load(item.pop("metadata_json", "{}"), {})\n        item["progress"] = int(item.get("progress") or 0)\n        if item.get("title"):\n            item["title"] = canonical_media_title(item["title"])\n        return item\n'''
    if row_anchor not in text:
        raise RuntimeError("DubStore row anchor missing")
    write(rel, text.replace(row_anchor, row_new, 1))

    # Notification query normalizes title evidence directly.
    rel = "services/dub_studio_runtime.py"
    text = read(rel)
    import_anchor = "from __future__ import annotations\n"
    if import_anchor not in text:
        raise RuntimeError("Dub runtime future-import anchor missing")
    text = text.replace(import_anchor, import_anchor + "\nfrom core.media_title_policy import canonical_media_title\n", 1)
    notif_anchor = '''        item["payload"] = payload if isinstance(payload, dict) else {}\n        result.append(item)\n'''
    notif_new = '''        item["payload"] = payload if isinstance(payload, dict) else {}\n        if item.get("project_title"):\n            item["project_title"] = canonical_media_title(item["project_title"])\n        result.append(item)\n'''
    if notif_anchor not in text:
        raise RuntimeError("notification title anchor missing")
    write(rel, text.replace(notif_anchor, notif_new, 1))

    # Delivery owns public filenames.
    rel = "handlers/dub_delivery.py"
    text = read(rel)
    import_anchor = "from __future__ import annotations\n"
    if import_anchor not in text:
        raise RuntimeError("dub_delivery future-import anchor missing")
    text = text.replace(import_anchor, import_anchor + "\nfrom core.media_title_policy import canonical_delivery_filename\n", 1)
    old = '''def available_outputs(project: dict[str, Any], *, include_all_video: bool = False) -> list[dict[str, Any]]:\n    dynamic = _dynamic_outputs(project, include_all_video=include_all_video)\n    return dynamic if dynamic else _recipe_outputs(project, include_all_video=include_all_video)\n'''
    new = '''def available_outputs(project: dict[str, Any], *, include_all_video: bool = False) -> list[dict[str, Any]]:\n    dynamic = _dynamic_outputs(project, include_all_video=include_all_video)\n    rows = dynamic if dynamic else _recipe_outputs(project, include_all_video=include_all_video)\n    for row in rows:\n        row["filename"] = canonical_delivery_filename(row.get("filename") or "")\n    return rows\n'''
    if old not in text:
        raise RuntimeError("available_outputs anchor missing")
    write(rel, text.replace(old, new, 1))

    # LiveDub heading uses the same canonical policy directly.
    rel = "services/livedub_output_policy.py"
    text = read(rel)
    import_anchor = "from __future__ import annotations\n"
    if import_anchor not in text:
        raise RuntimeError("livedub output future-import anchor missing")
    text = text.replace(import_anchor, import_anchor + "\nfrom core.media_title_policy import canonical_media_title\n", 1)
    pattern = re.compile(r'def _russian_heading_case\(value: str\) -> str:\n.*?(?=\n\ndef )', re.DOTALL)
    text, count = pattern.subn(
        lambda _m: 'def _russian_heading_case(value: str) -> str:\n    """Apply the canonical project title policy at the output owner."""\n    return canonical_media_title(value)\n',
        text,
        count=1,
    )
    if count != 1:
        raise RuntimeError("livedub heading function anchor missing")
    write(rel, text)

    # VoxCPM title owner directly applies canonical casing.
    rel = "tools/voxcpm2/generic_short_runtime.py"
    text = read(rel)
    text = text.replace("from core.text_utils import title_case_fragment\n", "from core.text_utils import title_case_fragment\nfrom core.media_title_policy import canonical_media_title\n", 1)
    old_return = '    return title_case_fragment(re.sub(r"\\s+", " ", title).strip())\n'
    new_return = '    return canonical_media_title(title_case_fragment(re.sub(r"\\s+", " ", title).strip()))\n'
    if old_return not in text:
        raise RuntimeError("generic_short_runtime title return anchor missing")
    text = text.replace(old_return, new_return, 1)
    patch_pattern = re.compile(r'\n\ndef _install_project_title_standard\(\) -> None:\n.*?(?=\n\ndef _ytdlp_base)', re.DOTALL)
    text, count = patch_pattern.subn("", text, count=1)
    if count != 1:
        raise RuntimeError("project-title runtime installer anchor missing")
    write(rel, text)

    # Project title generation calls its title owner directly; no sys.modules patch needed.
    rel = "tools/voxcpm2/generic_project_runtime.py"
    text = read(rel)
    old = '    fallback = f"Видео {video_id}"\n    return safe_russian_filename(title, fallback=fallback)\n'
    new = '''    fallback = f"Видео {video_id}"\n    context = json.dumps(metadata, ensure_ascii=False, sort_keys=True)\n    title = hardened.standardize_russian_title(title, context=context)\n    return safe_russian_filename(title, fallback=fallback)\n'''
    if old not in text:
        raise RuntimeError("generic_project_runtime title anchor missing")
    write(rel, text.replace(old, new, 1))

    # Clean routes no longer install or request title patches.
    for rel in (
        "tools/voxcpm2/_generic_clean_direct_runtime_base.py",
        "tools/voxcpm2/generic_clean_custom_runtime.py",
        "tools/voxcpm2/generic_clean_direct_runtime.py",
        "tools/voxcpm2/generic_clean_gemini_runtime.py",
    ):
        text = read(rel)
        text = re.sub(r'^from services\.dub_title_policy import install_voxcpm_title_policy\n', '', text, flags=re.MULTILINE)
        text = re.sub(r'^\s*install_voxcpm_title_policy\(hardened\)\n', '', text, flags=re.MULTILINE)
        text = re.sub(r'^\s*hardened\._install_project_title_standard\(\)\n', '', text, flags=re.MULTILINE)
        text = re.sub(r'^if not callable\(install_voxcpm_title_policy\):\n\s+raise RuntimeError\([^\n]+\)\n', '', text, flags=re.MULTILINE)
        write(rel, text)

    # Health reads pure policy + release contract directly.
    rel = "handlers/dub_health.py"
    text = read(rel)
    text = text.replace('        "title": repo / "services" / "dub_title_policy.py",\n', '        "title": repo / "core" / "media_title_policy.py",\n', 1)
    text = text.replace('        "single-title-policy": "install_dub_title_policy" in text["title"],\n', '        "single-title-policy": ("def canonical_media_title(" in text["title"] and "def canonical_delivery_filename(" in text["title"]),\n', 1)
    health_anchor = '''    quality_ok, quality_detail = _quality_contract(Path(__file__).resolve().parents[1])\n    checks.append(\n        _check(\n            "Clean Expressive NoChew + независимый QA",\n            quality_ok,\n            quality_detail\n'''
    health_new = '''    repo = Path(__file__).resolve().parents[1]\n    quality_ok, quality_detail = _quality_contract(repo)\n    from core.media_title_policy import media_title_policy_contract\n    from services.dub_release_health_v64 import _v68_quality_contract\n    title_ok, title_detail = media_title_policy_contract()\n    release_ok, release_detail = _v68_quality_contract(repo)\n    checks.append(\n        _check(\n            "Clean Expressive NoChew + независимый QA",\n            quality_ok and title_ok and release_ok,\n            quality_detail + "; " + title_detail + "; " + release_detail\n'''
    if health_anchor not in text:
        raise RuntimeError("Dub health composition anchor missing")
    text = text.replace(health_anchor, health_new, 1)
    write(rel, text)

    # Release health remains a pure checker; delete its hook-on-installer layer.
    rel = "services/dub_release_health_v64.py"
    text = read(rel)
    text = text.replace("from typing import Any, Callable\n", "from typing import Any\n")
    text = text.replace("_HOOKED = False\n", "")
    pattern = re.compile(r'\n\ndef _upgrade_monolithic_contract\(title: Any\) -> None:\n.*?(?=\n\n__all__ =)', re.DOTALL)
    text, count = pattern.subn("", text, count=1)
    if count != 1:
        raise RuntimeError("release health hook block missing")
    text = text.replace('    "install_release_health_hook",\n', "")
    write(rel, text)

    # Runtime manifest no longer owns title behavior.
    rel = "services/runtime_manifest.py"
    text = remove_manifest_feature(read(rel), "dub-title-policy")
    write(rel, text)

    # Old patch modules/package are gone: there is exactly one policy owner.
    for rel in ("services/dub_title_policy.py", "services/dub_title_policy/__init__.py"):
        path = ROOT / rel
        if not path.is_file():
            raise RuntimeError(f"expected old title policy path missing: {rel}")
        path.unlink()
        print(f"deleted {rel}")

    # Rewrite title tests around direct source ownership instead of installer strings.
    tests = '''from __future__ import annotations\n\nfrom pathlib import Path\n\nfrom core.media_title_policy import canonical_delivery_filename, canonical_media_title\n\nROOT = Path(__file__).resolve().parents[1]\n\n\ndef test_uppercase_one_letter_conjunction_is_not_an_acronym() -> None:\n    assert canonical_media_title("Сила И Достоинство Благочестивой Женщины - Джон Пайпер") == "Сила и Достоинство Благочестивой Женщины - Джон Пайпер"\n\n\ndef test_all_internal_russian_service_words_are_lowercase() -> None:\n    assert canonical_media_title("Вопросы И Ответы О Браке И Семье") == "Вопросы и Ответы о Браке и Семье"\n    assert canonical_media_title("И Это Только Начало") == "И Это Только Начало"\n    assert canonical_media_title("Путь К Богу Через Христа") == "Путь к Богу через Христа"\n\n\ndef test_acronyms_and_internal_case_are_preserved() -> None:\n    assert canonical_media_title("Q&A О LSB И МакАртуре") == "Q&A о LSB и МакАртуре"\n\n\ndef test_title_policy_preserves_semantic_punctuation() -> None:\n    assert canonical_media_title("Сомнение — Это Не Просто Слабость - Пол Вошер") == "Сомнение — Это не Просто Слабость - Пол Вошер"\n    assert canonical_media_title("Сомнение – Это Не Просто Слабость - Пол Вошер") == "Сомнение – Это не Просто Слабость - Пол Вошер"\n\n\ndef test_historical_manifest_filename_is_fixed_without_rerender() -> None:\n    filename = "Сила И Достоинство Благочестивой Женщины - Джон Пайпер — только русский голос.mp4"\n    assert canonical_delivery_filename(filename) == "Сила и Достоинство Благочестивой Женщины - Джон Пайпер — только русский голос.mp4"\n\n\ndef test_core_title_owner_calls_canonical_policy_directly() -> None:\n    import core.text_utils as text_utils\n    assert text_utils.title_case_fragment("the power of grace") == "The Power of Grace"\n    assert text_utils.title_case_fragment("Сила И Достоинство") == "Сила и Достоинство"\n    assert text_utils.title_case_fragment("Сомнение — Это Не Слабость") == "Сомнение — Это не Слабость"\n\n\ndef test_title_policy_is_source_owned_across_public_surfaces() -> None:\n    expected = {\n        "core/text_utils.py": "canonical_media_title",\n        "services/dub_studio.py": "canonical_media_title",\n        "services/dub_studio_runtime.py": "canonical_media_title",\n        "handlers/dub_delivery.py": "canonical_delivery_filename",\n        "services/livedub_output_policy.py": "canonical_media_title",\n        "tools/voxcpm2/generic_short_runtime.py": "canonical_media_title",\n    }\n    for rel, marker in expected.items():\n        source = (ROOT / rel).read_text(encoding="utf-8")\n        assert marker in source\n    manifest = (ROOT / "services/runtime_manifest.py").read_text(encoding="utf-8")\n    assert "dub-title-policy" not in manifest\n    assert "install_dub_title_policy" not in manifest\n'''
    write("tests/test_dub_title_policy.py", tests)

    print("title policy migrated to explicit source ownership")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
