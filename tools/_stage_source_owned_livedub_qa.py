#!/usr/bin/env python3
"""One-shot deterministic codemod for source-owned LiveDub QA/provenance."""
from pathlib import Path
import re


def read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    Path(path).write_text(text, encoding="utf-8")


# ── Manifest: one QA contract, no QA/provenance patch installers. ──────────
path = "services/runtime_manifest.py"
text = read(path)
for feature_id in (
    "livedub-long-qa",
    "livedub-qa-trust",
    "livedub-ru-provenance",
    "livedub-qa-hardening",
):
    pattern = rf'\n    RuntimeFeature\(\n        "{re.escape(feature_id)}",.*?\n    \),'
    text, count = re.subn(pattern, "", text, count=1, flags=re.S)
    if count != 1:
        raise SystemExit(f"runtime manifest feature not found: {feature_id}")
anchor = '''    RuntimeFeature(
        "livedub-delivery-contract",
        "services.livedub_delivery_coordinator",
        "validate_livedub_delivery_contract",
        RuntimePhase.POST_MAIN,
    ),'''
if text.count(anchor) != 1:
    raise SystemExit("runtime manifest delivery contract anchor mismatch")
text = text.replace(
    anchor,
    '''    RuntimeFeature(
        "livedub-qa-contract",
        "services.livedub_qa",
        "validate_livedub_qa_contract",
        RuntimePhase.POST_MAIN,
    ),
''' + anchor,
    1,
)
write(path, text)

# Keep the old Quick-QA reach that the long-QA installer used to set, without
# importing core.globals before main.
path = "services/pre_main_policy.py"
text = read(path)
if "import os\n" not in text:
    text = text.replace("from __future__ import annotations\n", "from __future__ import annotations\n\nimport os\n", 1)
anchor = '''    qa = configure_gemini_qa_policy()
    maximum = configure_max_quality_env()'''
replacement = '''    os.environ.setdefault("LIVEDUB_QUICK_QA_MAX_DURATION", "10800")
    qa = configure_gemini_qa_policy()
    maximum = configure_max_quality_env()'''
if text.count(anchor) != 1:
    raise SystemExit("pre-main Quick-QA policy anchor mismatch")
text = text.replace(anchor, replacement, 1)
write(path, text)

# ── RU provenance: pure recorder/reader; no wrapping of Yandex or mix. ─────
path = "services/livedub_ru_provenance.py"
text = read(path)
text = text.replace("import functools\n", "")
text = text.replace("_LOCK = threading.Lock()\n_INSTALLED = False\n", "")
# Keep threading for temp file IDs.
pattern = r'\ndef _install_vot_recorder\(\) -> None:.*\Z'
replacement = '''

def snapshot_ru_audio_candidates(workdir: Path | str) -> dict[str, tuple[int, int]]:
    return _snapshot_mp3(Path(workdir))


def record_returned_ru_audio(
    result: Path | str,
    *,
    workdir: Path | str,
    before: dict[str, tuple[int, int]],
    voice_style: str = "live",
) -> bool:
    """Record only a new/changed MP3 returned by the current VOT call."""
    try:
        root = Path(workdir)
        candidate = Path(result)
        if candidate.parent.resolve() != root.resolve():
            logger.warning(
                "[LiveDubProvenance] returned MP3 is outside requested output_dir: %s",
                candidate,
            )
            return False
        after = _file_state(candidate)
        if after is None or before.get(candidate.name) == after:
            logger.warning(
                "[LiveDubProvenance] unchanged pre-existing MP3 not recorded: %s",
                candidate.name,
            )
            return False
        saved = write_ru_audio_provenance(candidate, voice_style=voice_style)
        if saved:
            logger.info("[LiveDubProvenance] exact VOT RU source recorded: %s", candidate.name)
        return saved
    except Exception as exc:
        logger.warning("[LiveDubProvenance] recorder skipped: %s", str(exc)[:180])
        return False


def install_livedub_ru_provenance() -> str:
    """Compatibility validator; provenance is source-owned by producer/consumer."""
    if not callable(write_ru_audio_provenance) or not callable(read_ru_audio_provenance):
        raise RuntimeError("LiveDub RU provenance helpers are unavailable")
    return "source-owned VOT provenance; no producer/consumer wrapping"


__all__ = [
    "install_livedub_ru_provenance",
    "read_ru_audio_provenance",
    "record_returned_ru_audio",
    "snapshot_ru_audio_candidates",
    "write_ru_audio_provenance",
]
'''
text, count = re.subn(pattern, replacement, text, count=1, flags=re.S)
if count != 1:
    raise SystemExit(f"provenance installer removal count={count}")
write(path, text)

# Yandex producer records exact successful returned MP3 directly.
path = "services/yandex_live_dub.py"
text = read(path)
anchor = '''    # ── Путь 1: новый протокол (@vot.js/node, OAuth) ──
    helper = await asyncio.get_running_loop().run_in_executor(None, _ensure_vot_helper)'''
replacement = '''    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    from services.livedub_ru_provenance import (
        record_returned_ru_audio,
        snapshot_ru_audio_candidates,
    )
    _ru_before = snapshot_ru_audio_candidates(output_dir)

    # ── Путь 1: новый протокол (@vot.js/node, OAuth) ──
    helper = await asyncio.get_running_loop().run_in_executor(None, _ensure_vot_helper)'''
if text.count(anchor) != 1:
    raise SystemExit("Yandex provenance start anchor mismatch")
text = text.replace(anchor, replacement, 1)
old = '''            return await _get_audio_new_protocol(
                helper, video_url, output_dir,
                timeout=max(timeout, 900), voice_style=voice_style, duration=duration,
                lang=lang,
            )'''
new = '''            _new_result = await _get_audio_new_protocol(
                helper, video_url, output_dir,
                timeout=max(timeout, 900), voice_style=voice_style, duration=duration,
                lang=lang,
            )
            record_returned_ru_audio(
                _new_result,
                workdir=output_dir,
                before=_ru_before,
                voice_style=voice_style,
            )
            return _new_result'''
if text.count(old) != 1:
    raise SystemExit("Yandex new-protocol return anchor mismatch")
text = text.replace(old, new, 1)
old = '''    logger.info(f"[LiveDub] Готово аудио: {downloaded_path}")
    return downloaded_path'''
new = '''    record_returned_ru_audio(
        downloaded_path,
        workdir=output_dir,
        before=_ru_before,
        voice_style=voice_style,
    )
    logger.info(f"[LiveDub] Готово аудио: {downloaded_path}")
    return downloaded_path'''
if text.count(old) != 1:
    raise SystemExit("Yandex legacy return anchor mismatch")
text = text.replace(old, new, 1)
write(path, text)

# Mix consumer prefers exact provenance directly, then role-safe fallback.
path = "services/livedub_mix.py"
text = read(path)
old = '''    from services.livedub_audio_quality_guard import select_clean_translation_mp3

    return orig, select_clean_translation_mp3(workdir)'''
new = '''    from services.livedub_audio_quality_guard import select_clean_translation_mp3
    from services.livedub_ru_provenance import read_ru_audio_provenance

    exact = read_ru_audio_provenance(workdir)
    return orig, exact or select_clean_translation_mp3(workdir)'''
if text.count(old) != 1:
    raise SystemExit("livedub_mix provenance consumer anchor mismatch")
text = text.replace(old, new, 1)
write(path, text)

# ── Long QA: callable strategy + report decorator, no module assignment. ───
path = "services/livedub_long_qa.py"
text = read(path)
text = text.replace("_ORIGINAL_RUN = None\n_ORIGINAL_FORMAT = None\n", "")
pattern = r'\ndef install_livedub_long_qa\(\) -> None:.*\Z'
replacement = '''

async def run_long_translation_qa(
    base_runner,
    *,
    dub_video_path: Path,
    original_audio_path: Optional[Path],
    ai_data: Optional[dict],
    duration: int,
    model_name: str = "",
    dub_srt_path: Optional[Path] = None,
    dub_audio_path: Optional[Path] = None,
    existing_audio_part=None,
    existing_client=None,
    thinking_level: str = "high",
) -> Optional[dict]:
    """Use segmented complete coverage only when the recording crosses the threshold."""
    common = dict(
        dub_video_path=dub_video_path,
        original_audio_path=original_audio_path,
        ai_data=ai_data,
        duration=duration,
        model_name=model_name,
        dub_srt_path=dub_srt_path,
        dub_audio_path=dub_audio_path,
        existing_audio_part=existing_audio_part,
        existing_client=existing_client,
        thinking_level=thinking_level,
    )
    threshold = _env_int("LIVEDUB_LONG_QA_THRESHOLD_SEC", 480, 120, 3600)
    if not _enabled() or not duration or duration <= threshold:
        return await base_runner(**common)
    return await _run_long_qa(base_runner, **common)


def decorate_segment_report(text: str, qa: dict[str, Any]) -> str:
    if not isinstance(qa, dict) or not qa.get("_segmented"):
        return text
    checked = int(qa.get("_segments_checked") or 0)
    total = int(qa.get("_segments_total") or 0)
    coverage = float(qa.get("_coverage_ratio") or 0) * 100
    note = (
        f"⚠️ Сегментная проверка частичная: {checked}/{total}, покрытие {coverage:.0f}%."
        if qa.get("_segmented_partial")
        else f"🧩 Вся запись проверена по сегментам: {checked}/{total}, покрытие {coverage:.0f}%."
    )
    lines = str(text or "").splitlines()
    lines.insert(1 if lines else 0, note)
    try:
        from converters.md_telegraph import safe_trim_caption
        return safe_trim_caption("\n".join(lines), 3900)
    except Exception:
        return "\n".join(lines)[:3900]


def install_livedub_long_qa() -> str:
    """Compatibility validator; long QA is called by the QA owner."""
    if not callable(run_long_translation_qa):
        raise RuntimeError("long LiveDub QA strategy is unavailable")
    return "source-owned segmented LiveDub QA strategy"


__all__ = [
    "aggregate_segment_results",
    "decorate_segment_report",
    "install_livedub_long_qa",
    "run_long_translation_qa",
    "segment_windows",
]
'''
text, count = re.subn(pattern, replacement, text, count=1, flags=re.S)
if count != 1:
    raise SystemExit(f"long QA installer removal count={count}")
write(path, text)

# ── QA hardening: pure source preparation/annotation/report policy. ─────────
path = "services/livedub_qa_hardening.py"
text = read(path)
pattern = r'\ndef install_qa_hardening\(\) -> None:.*\Z'
replacement = '''

def prepare_exact_timeline_inputs(options: dict[str, Any]) -> tuple[dict[str, Any], Path | None]:
    """Prefer the untouched source video when available in the LiveDub workdir."""
    prepared = dict(options)
    exact = _exact_original_in_workdir(prepared.get("dub_video_path"))
    if exact is not None:
        prepared["original_audio_path"] = exact
        prepared["existing_audio_part"] = None
        prepared["existing_client"] = None
    return prepared, exact


def annotate_qa_availability(
    result: dict[str, Any],
    options: dict[str, Any],
    exact_original: Path | None,
) -> dict[str, Any]:
    data = dict(result)
    local_original = _local_file(options.get("original_audio_path"))
    reused_original = _active_part(options.get("existing_audio_part")) and options.get("existing_client") is not None
    data["_qa_availability_audited"] = True
    data["_qa_exact_timeline_original"] = exact_original is not None
    data["_qa_original_reference_available"] = bool(local_original or reused_original)
    data["_qa_local_original_available"] = local_original
    data["_qa_reused_original_available"] = reused_original
    data["_qa_russian_audio_available"] = bool(
        _local_file(options.get("dub_audio_path")) or _local_file(options.get("dub_video_path"))
    )
    return data


def decorate_hardened_report(text: str, data: dict[str, Any]) -> str:
    rendered = str(text or "")
    if data.get("_qa_availability_audited"):
        rendered = rendered.replace(_OLD_LOW_CONFIDENCE_NOTE + "\n", "")
        rendered = rendered.replace(_OLD_LOW_CONFIDENCE_NOTE, "")
        notes: list[str] = []
        if not data.get("_qa_original_reference_available"):
            notes.append(
                "⚠️ Английский оригинал не был доступен как аудио; итог мог "
                "опираться только на ограниченный конспект и не является полной сверкой."
            )
        elif not data.get("_qa_local_original_available") and data.get("_qa_confirmation_failed"):
            notes.append(
                "⚠️ Первый проход видел английский оригинал через Gemini, но "
                "локального файла для точечной перепроверки уже не осталось."
            )
        if not data.get("_qa_russian_audio_available"):
            notes.append(
                "⚠️ Фактически отправленная русская дорожка недоступна для проверки; "
                "оценка и неподтверждённые замечания скрыты."
            )
        rendered = _insert_after_head(rendered, notes)
    if not (data.get("issues") or []):
        rendered = rendered.replace(
            "✅ Показанные замечания прошли точечную перепроверку",
            "✅ Первичные подозрения прошли точечную перепроверку и не подтвердились",
        )
    omitted = int(data.get("_qa_verification_limit_dropped") or 0)
    if omitted:
        rendered = _insert_after_head(
            rendered,
            [
                f"⚠️ За пределами настроенного лимита осталось замечаний: {omitted}; "
                "они не опубликованы и не применены автоматически."
            ],
        )
    return rendered


def install_qa_hardening() -> str:
    """Compatibility validator; hardening is consumed directly by QA/trust."""
    if not callable(confirmed_result_one_to_one):
        raise RuntimeError("strict QA confirmation policy is unavailable")
    return "source-owned one-to-one QA hardening policy"


__all__ = [
    "annotate_qa_availability",
    "confirmed_result_one_to_one",
    "decorate_hardened_report",
    "install_qa_hardening",
    "issues_match_strict",
    "prepare_exact_timeline_inputs",
]
'''
text, count = re.subn(pattern, replacement, text, count=1, flags=re.S)
if count != 1:
    raise SystemExit(f"QA hardening installer removal count={count}")
write(path, text)

# ── QA trust: public strategy; strict one-to-one policy called directly. ────
path = "services/livedub_qa_trust.py"
text = read(path)
text = text.replace("import threading\n", "")
text = text.replace("_INSTALL_LOCK = threading.Lock()\n", "")
pattern = r'def _confirmed_result\(primary: dict\[str, Any\], validation: dict\[str, Any\]\) -> dict\[str, Any\]:.*?\n\ndef _unconfirmed_failure_result'
replacement = '''def _confirmed_result(primary: dict[str, Any], validation: dict[str, Any]) -> dict[str, Any]:
    from services.livedub_qa_hardening import confirmed_result_one_to_one

    return confirmed_result_one_to_one(primary, validation)


def _unconfirmed_failure_result'''
text, count = re.subn(pattern, replacement, text, count=1, flags=re.S)
if count != 1:
    raise SystemExit(f"trust confirmation policy replacement count={count}")
old = '    max_issues = _env_int("LIVEDUB_QA_VERIFY_MAX_ISSUES", 8, 1, 16)'
new = '''    max_issues = _env_int("LIVEDUB_QA_VERIFY_MAX_ISSUES", 20, 1, 40)
    total_candidates = len(issues)
    omitted_by_limit = max(0, total_candidates - max_issues)'''
if text.count(old) != 1:
    raise SystemExit("trust max issues anchor mismatch")
text = text.replace(old, new, 1)
old = '''    if clean_ru is None:
        # The final mix contains quiet English beneath Russian. It is useful for
        # review, but not safe enough for destructive automatic muting.'''
new = '''    if omitted_by_limit:
        result["_qa_candidate_count_total"] = total_candidates
        result["_qa_verification_limit_dropped"] = omitted_by_limit
        result["_qa_unconfirmed_dropped"] = int(result.get("_qa_unconfirmed_dropped") or 0) + omitted_by_limit
        result["_low_confidence"] = True

    if clean_ru is None:
        # The final mix contains quiet English beneath Russian. It is useful for
        # review, but not safe enough for destructive automatic muting.'''
if text.count(old) != 1:
    raise SystemExit("trust omitted-limit annotation anchor mismatch")
text = text.replace(old, new, 1)
pattern = r'\ndef install_livedub_qa_trust\(\) -> None:.*\Z'
replacement = '''

def audio_trust_enabled() -> bool:
    return _enabled()


async def apply_audio_trust(
    base_runner,
    *,
    primary: dict[str, Any] | None,
    dub_video_path: Path,
    original_audio_path: Optional[Path],
    duration: int,
    model_name: str = "",
    dub_audio_path: Optional[Path] = None,
    existing_audio_part=None,
    existing_client=None,
) -> Optional[dict]:
    """Confirm broad-pass candidates against fresh local audio windows."""
    if not isinstance(primary, dict) or not _enabled():
        return primary
    result = dict(primary)
    original_available = bool(
        _local_path(original_audio_path)
        or (_active_audio_part(existing_audio_part) and existing_client is not None)
    )
    russian_available = bool(_local_path(dub_audio_path) or _local_path(dub_video_path))
    result["_qa_audio_grounded"] = original_available and russian_available
    result["_qa_confirmation_passes"] = 1
    if not result["_qa_audio_grounded"]:
        result["_low_confidence"] = True

    issues = _clean_issues(result.get("issues"))
    if not issues or not _confirmation_enabled():
        return result
    if not original_available or not russian_available:
        return _unconfirmed_failure_result(result, "нет обеих фактически звучащих дорожек")

    logger.info(
        "[LiveDubQATrust] %d candidate issue(s): focused audio verification",
        len(issues),
    )
    verified = await _verify_candidate_windows(
        base_runner,
        primary=result,
        dub_video_path=Path(dub_video_path),
        original_audio_path=original_audio_path,
        duration=int(duration or 0),
        model_name=model_name,
        dub_audio_path=dub_audio_path,
    )
    logger.info(
        "[LiveDubQATrust] confirmed=%d dropped=%d windows=%d/%d",
        len(verified.get("issues") or []),
        int(verified.get("_qa_unconfirmed_dropped") or 0),
        int(verified.get("_qa_verification_windows") or 0),
        int(verified.get("_qa_verification_windows_total") or 0),
    )
    return verified


def decorate_trust_report(text: str, qa: dict[str, Any]) -> str:
    return _insert_report_notes(text, qa)


def install_livedub_qa_trust() -> str:
    """Compatibility validator; trust is called by the QA owner."""
    if not callable(apply_audio_trust):
        raise RuntimeError("LiveDub QA trust strategy is unavailable")
    return "source-owned full-scan + focused-audio trust strategy"


__all__ = [
    "apply_audio_trust",
    "audio_trust_enabled",
    "decorate_trust_report",
    "install_livedub_qa_trust",
]
'''
text, count = re.subn(pattern, replacement, text, count=1, flags=re.S)
if count != 1:
    raise SystemExit(f"QA trust installer removal count={count}")
write(path, text)

# ── QA owner: base comparison + direct orchestration + report decorators. ──
path = "services/livedub_qa.py"
text = read(path)
old = "async def run_translation_qa(\n"
if text.count(old) != 1:
    raise SystemExit(f"QA public base definition count={text.count(old)}")
text = text.replace(old, "async def _run_translation_qa_base(\n", 1)
marker = '''

# ══════════════════════════════════════════════════════════════
#  3. Форматирование отчёта
# ══════════════════════════════════════════════════════════════
'''
if text.count(marker) != 1:
    raise SystemExit("QA formatting marker mismatch")
public_runner = '''

async def run_translation_qa(
    dub_video_path: Path,
    original_audio_path: Optional[Path],
    ai_data: Optional[dict],
    duration: int,
    model_name: str = "",
    dub_srt_path: Optional[Path] = None,
    dub_audio_path: Optional[Path] = None,
    existing_audio_part=None,
    existing_client=None,
    thinking_level: str = "high",
) -> Optional[dict]:
    """Source-owned QA pipeline: evidence -> coverage -> confirmation."""
    from services.livedub_long_qa import run_long_translation_qa
    from services.livedub_qa_hardening import (
        annotate_qa_availability,
        prepare_exact_timeline_inputs,
    )
    from services.livedub_qa_trust import apply_audio_trust, audio_trust_enabled

    options = dict(
        dub_video_path=Path(dub_video_path),
        original_audio_path=original_audio_path,
        ai_data=ai_data,
        duration=int(duration or 0),
        model_name=model_name,
        dub_srt_path=None if audio_trust_enabled() else dub_srt_path,
        dub_audio_path=dub_audio_path,
        existing_audio_part=existing_audio_part,
        existing_client=existing_client,
        thinking_level=thinking_level,
    )
    options, exact_original = prepare_exact_timeline_inputs(options)
    primary = await run_long_translation_qa(_run_translation_qa_base, **options)
    if not isinstance(primary, dict):
        return primary
    primary = annotate_qa_availability(primary, options, exact_original)
    return await apply_audio_trust(
        _run_translation_qa_base,
        primary=primary,
        dub_video_path=options["dub_video_path"],
        original_audio_path=options["original_audio_path"],
        duration=options["duration"],
        model_name=options["model_name"],
        dub_audio_path=options["dub_audio_path"],
        existing_audio_part=options["existing_audio_part"],
        existing_client=options["existing_client"],
    )
'''
text = text.replace(marker, public_runner + marker, 1)
old = "def format_qa_report(qa: dict, video_url: str = \"\") -> str:\n"
if text.count(old) != 1:
    raise SystemExit("QA report definition mismatch")
text = text.replace(old, "def _format_qa_report_base(qa: dict, video_url: str = \"\") -> str:\n", 1)
# Append public report wrapper and validator after the base formatter.
text += '''


def format_qa_report(qa: dict, video_url: str = "") -> str:
    """Render one truthful report through pure source-owned decorators."""
    from services.livedub_long_qa import decorate_segment_report
    from services.livedub_qa_hardening import decorate_hardened_report
    from services.livedub_qa_trust import decorate_trust_report

    text = _format_qa_report_base(qa, video_url=video_url)
    text = decorate_segment_report(text, qa)
    text = decorate_trust_report(text, qa)
    text = decorate_hardened_report(text, qa)
    try:
        from converters.md_telegraph import safe_trim_caption
        return safe_trim_caption(text, 3900)
    except Exception:
        return text[:3900]


def validate_livedub_qa_contract() -> str:
    """Startup invariant for the direct QA pipeline."""
    from services.livedub_long_qa import run_long_translation_qa
    from services.livedub_qa_hardening import confirmed_result_one_to_one
    from services.livedub_qa_trust import apply_audio_trust

    if not all(callable(item) for item in (
        _run_translation_qa_base,
        run_translation_qa,
        run_long_translation_qa,
        apply_audio_trust,
        confirmed_result_one_to_one,
        format_qa_report,
    )):
        raise RuntimeError("source-owned LiveDub QA contract is incomplete")
    return "source-owned LiveDub QA: base -> segmented coverage -> focused confirmation"
'''
write(path, text)

# ── Regression contract: active QA/provenance modules cannot reintroduce hooks.
write(
    "tests/test_source_owned_livedub_qa_architecture.py",
    '''from __future__ import annotations

import asyncio
from pathlib import Path

import services.livedub_long_qa as long_qa
import services.livedub_qa as qa
import services.livedub_qa_hardening as hardening
import services.livedub_qa_trust as trust

ROOT = Path(__file__).resolve().parents[1]


def _src(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_manifest_has_one_source_owned_qa_contract():
    src = _src("services/runtime_manifest.py")
    for feature in (
        "livedub-long-qa",
        "livedub-qa-trust",
        "livedub-ru-provenance",
        "livedub-qa-hardening",
    ):
        assert f'"{feature}"' not in src
    assert '"livedub-qa-contract"' in src


def test_qa_strategy_modules_do_not_assign_into_other_modules():
    forbidden = (
        "module.run_translation_qa =",
        "module.format_qa_report =",
        "qa._issues_match =",
        "qa._confirmed_result =",
        "qa._env_int =",
        "qa._verify_candidate_windows =",
        "qa._insert_report_notes =",
        "yandex.get_live_dub_audio =",
        "mix.find_pro_tracks =",
    )
    for rel in (
        "services/livedub_long_qa.py",
        "services/livedub_qa_trust.py",
        "services/livedub_qa_hardening.py",
        "services/livedub_ru_provenance.py",
    ):
        src = _src(rel)
        assert "sys.meta_path" not in src
        assert all(token not in src for token in forbidden)


def test_public_qa_owner_forces_audio_truth_then_calls_long_and_trust(monkeypatch, tmp_path):
    seen = {}

    async def base(**kwargs):
        raise AssertionError("base runner should be delegated through long strategy")

    async def fake_long(base_runner, **kwargs):
        seen["long_base"] = base_runner
        seen["long"] = dict(kwargs)
        return {"issues": [{"time": "00:10", "severity": "minor"}]}

    async def fake_trust(base_runner, **kwargs):
        seen["trust_base"] = base_runner
        seen["trust"] = dict(kwargs)
        result = dict(kwargs["primary"])
        result["trusted"] = True
        return result

    monkeypatch.setattr(qa, "_run_translation_qa_base", base)
    monkeypatch.setattr(long_qa, "run_long_translation_qa", fake_long)
    monkeypatch.setattr(trust, "apply_audio_trust", fake_trust)
    monkeypatch.setattr(trust, "audio_trust_enabled", lambda: True)
    monkeypatch.setattr(hardening, "prepare_exact_timeline_inputs", lambda options: (dict(options), None))
    monkeypatch.setattr(hardening, "annotate_qa_availability", lambda result, options, exact: dict(result, audited=True))

    video = tmp_path / "dub.mp4"
    video.write_bytes(b"x")

    result = asyncio.run(qa.run_translation_qa(
        dub_video_path=video,
        original_audio_path=None,
        ai_data=None,
        duration=600,
        dub_srt_path=tmp_path / "untrusted.srt",
        dub_audio_path=None,
    ))
    assert result and result["trusted"] is True
    assert seen["long_base"] is base
    assert seen["trust_base"] is base
    assert seen["long"]["dub_srt_path"] is None
    assert seen["trust"]["primary"]["audited"] is True


def test_provenance_is_called_by_producer_and_consumer_directly():
    yandex = _src("services/yandex_live_dub.py")
    mix = _src("services/livedub_mix.py")
    provenance = _src("services/livedub_ru_provenance.py")
    assert "record_returned_ru_audio(" in yandex
    assert "snapshot_ru_audio_candidates(" in yandex
    assert "read_ru_audio_provenance(workdir)" in mix
    assert "yandex.get_live_dub_audio =" not in provenance
    assert "mix.find_pro_tracks =" not in provenance


def test_pre_main_keeps_long_quick_qa_reach_without_importing_qa_owner():
    src = _src("services/pre_main_policy.py")
    assert 'LIVEDUB_QUICK_QA_MAX_DURATION", "10800"' in src
    assert "import services.livedub_qa" not in src
''',
)

# Append QA/provenance source ownership to durable audit.
audit = Path("docs/quality_audits/2026-08-15-explicit-livedub-delivery.md")
text = audit.read_text(encoding="utf-8")
text += '''

## Follow-up — QA/provenance ownership

The re-audit found four remaining LiveDub QA/provenance installers that still
replaced functions after import. They were removed from runtime composition too.
`livedub_qa.run_translation_qa` now owns the complete QA flow directly: base
Gemini audio comparison, long segmented coverage, exact-timeline evidence,
focused candidate confirmation and truthful report decoration. VOT provenance is
recorded by `get_live_dub_audio` and consumed by `find_pro_tracks` directly.
No QA/trust/provenance module assigns functions into another imported module.
'''
audit.write_text(text, encoding="utf-8")
