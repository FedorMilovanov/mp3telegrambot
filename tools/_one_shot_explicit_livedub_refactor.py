#!/usr/bin/env python3
"""One-shot branch codemod; deletes itself before the resulting commit."""
from pathlib import Path
import re


def read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    Path(path).write_text(text, encoding="utf-8")


def replace_once(path: str, old: str, new: str) -> None:
    text = read(path)
    count = text.count(old)
    if count != 1:
        raise SystemExit(
            f"{path}: expected exactly one literal match, got {count}: {old[:100]!r}"
        )
    write(path, text.replace(old, new, 1))


# ---------------------------------------------------------------------------
# Runtime manifest: one explicit lifecycle owner.  The former LiveDub Telegram
# interception stack is not part of production composition anymore.
# ---------------------------------------------------------------------------
path = "services/runtime_manifest.py"
text = read(path).replace(
    'RUNTIME_MANIFEST_POLICY = "declarative-runtime-composition-v1"',
    'RUNTIME_MANIFEST_POLICY = "declarative-runtime-composition-v2"',
)
remove_features = {
    "livedub-info-guard",
    "livedub-info-presentation",
    "livedub-delivery-hardening",
    "livedub-output-policy",
    "livedub-publication",
    "livedub-publication-diagnostics",
    "livedub-audio-companion",
    "livedub-audio-cache-recovery",
    "livedub-audio-quality",
    "livedub-new-delivery-atomicity",
    "livedub-cached-delivery-atomicity",
    "livedub-audio-dedupe",
    "livedub-audio-dedupe-hardening",
    "livedub-deep-audit",
    "livedub-dual-audio-policy",
}
for feature_id in remove_features:
    pattern = rf'\n    RuntimeFeature\(\n        "{re.escape(feature_id)}",.*?\n    \),'
    text, count = re.subn(pattern, "", text, count=1, flags=re.S)
    if count != 1:
        raise SystemExit(f"runtime_manifest: missing feature {feature_id}")

local_block = '''    RuntimeFeature(
        "local-bot-api",
        "services.local_botapi_required",
        "require_local_bot_api",
        RuntimePhase.PRE_MAIN,
    ),'''
if text.count(local_block) != 1:
    raise SystemExit("runtime_manifest: local-bot-api anchor mismatch")
explicit_pre = local_block + '''
    RuntimeFeature(
        "pre-main-quality-policy",
        "services.pre_main_policy",
        "configure_pre_main_policy",
        RuntimePhase.PRE_MAIN,
    ),
    RuntimeFeature(
        "polling-reliability",
        "services.polling_reliability_runtime",
        "install_polling_reliability_runtime",
        RuntimePhase.PRE_MAIN,
    ),
    RuntimeFeature(
        "shorts-visual-policy",
        "services.shorts_static_runtime",
        "install_short_static_runtime",
        RuntimePhase.PRE_MAIN,
    ),
    RuntimeFeature(
        "conspect-quality-bootstrap",
        "services.conspect_bootstrap",
        "configure_conspect_runtime",
        RuntimePhase.PRE_MAIN,
    ),'''
text = text.replace(local_block, explicit_pre, 1)

project_anchor = '''    RuntimeFeature(
        "project-runtime-hardening",
        "services.project_runtime_hardening",
        "install_project_runtime_hardening",
        RuntimePhase.POST_MAIN,
        requires_main=True,
    ),'''
if text.count(project_anchor) != 1:
    raise SystemExit("runtime_manifest: project hardening anchor mismatch")
explicit_post = '''    RuntimeFeature(
        "livedub-qa-hardening",
        "services.livedub_qa_hardening",
        "install_qa_hardening",
        RuntimePhase.POST_MAIN,
    ),
    RuntimeFeature(
        "livedub-delivery-contract",
        "services.livedub_delivery_coordinator",
        "validate_livedub_delivery_contract",
        RuntimePhase.POST_MAIN,
    ),
''' + project_anchor
text = text.replace(project_anchor, explicit_post, 1)
write(path, text)

# ---------------------------------------------------------------------------
# Coordinator polish + startup validator.
# ---------------------------------------------------------------------------
path = "services/livedub_delivery_coordinator.py"
text = read(path)
old = 'author = metadata_text(str(card.get("author") or canonical_author(""))) or ""'
if text.count(old) != 1:
    raise SystemExit("coordinator publication author anchor mismatch")
text = text.replace(old, 'author = metadata_text(str(card.get("author") or "")) or ""', 1)
old = 'enabled=bool(enabled and _truthy("LIVEDUB_AUDIO_DEDUPE")),'
if text.count(old) != 1:
    raise SystemExit("coordinator source deferral anchor mismatch")
text = text.replace(
    old,
    'enabled=bool(\n            enabled\n            and _truthy("LIVEDUB_AUDIO_DEDUPE")\n            and _truthy("LIVEDUB_SEND_AUDIO")\n        ),',
    1,
)
if "def validate_livedub_delivery_contract" not in text:
    text += '''


def validate_livedub_delivery_contract() -> str:
    """Fail startup when the explicit source-owned delivery surface regresses."""
    import services.livedub_audio_companion as companion
    from services.livedub_audio_cache_recovery import (
        load_recoverable_cache,
        save_recoverable_cache,
    )
    from services.livedub_audio_quality_guard import select_clean_translation_mp3
    from services.livedub_publication import build_publication_card, format_video_caption

    required = (
        companion._probe_audio,
        companion._extract_mix_mp3,
        companion._cache_get,
        companion._cache_put_variant,
        load_recoverable_cache,
        save_recoverable_cache,
        select_clean_translation_mp3,
        build_publication_card,
        format_video_caption,
        deliver_new_companions,
        deliver_cached_companions,
        create_source_audio_deferral,
    )
    if not all(callable(item) for item in required):
        raise RuntimeError("explicit LiveDub delivery contract is incomplete")
    return (
        "explicit pipeline delivery; source-owned cache/quality/publication; "
        "no Telegram send_* interception"
    )


__all__ = [
    "SourceAudioDeferral",
    "create_source_audio_deferral",
    "delete_message_best_effort",
    "deliver_cached_companions",
    "deliver_new_companions",
    "validate_livedub_delivery_contract",
]
'''
write(path, text)

# ---------------------------------------------------------------------------
# Companion owns its persistence/role helpers directly.  Its compatibility
# installer becomes a validator and never touches Bot/ExtBot.
# ---------------------------------------------------------------------------
path = "services/livedub_audio_companion.py"
text = read(path)
pattern = r'def _load_cache\(\) -> dict\[str, dict\[str, Any\]\]:.*?\n\ndef _normalise_cache_entry'
replacement = '''def _load_cache() -> dict[str, dict[str, Any]]:
    from services.livedub_audio_cache_recovery import load_recoverable_cache

    data = load_recoverable_cache(_cache_path())
    return data if isinstance(data, dict) else {}


def _save_cache(data: dict[str, dict[str, Any]]) -> None:
    from services.livedub_audio_cache_recovery import save_recoverable_cache

    try:
        save_recoverable_cache(_cache_path(), data)
    except Exception as exc:
        logger.warning("[LiveDubAudio] recoverable cache save failed: %s", str(exc)[:180])


def _normalise_cache_entry'''
text, count = re.subn(pattern, replacement, text, count=1, flags=re.S)
if count != 1:
    raise SystemExit(f"companion cache owner replacement count={count}")

pattern = r'def _find_clean_ru_track\(video_path: Path\) -> Path \| None:.*?\n\ndef _extract_mix_mp3'
replacement = '''def _find_clean_ru_track(video_path: Path) -> Path | None:
    from services.livedub_audio_quality_guard import select_clean_translation_mp3

    candidate = select_clean_translation_mp3(Path(video_path).parent)
    if candidate is None:
        return None
    ok, _duration = _probe_audio(candidate)
    return candidate if ok else None


def _extract_mix_mp3'''
text, count = re.subn(pattern, replacement, text, count=1, flags=re.S)
if count != 1:
    raise SystemExit(f"companion clean owner replacement count={count}")

pattern = r'\ndef _wrap_send_video\(cls: type\) -> None:.*\Z'
replacement = '''

def validate_livedub_audio_companion() -> str:
    """Compatibility/startup validator; performs no Telegram mutation."""
    if not callable(_cache_get) or not callable(_cache_put_variant):
        raise RuntimeError("LiveDub companion cache surface is incomplete")
    return "source-owned companion helpers; explicit coordinator delivery"


def install_livedub_audio_companion() -> str:
    """Deprecated compatibility name; no longer patches Bot/ExtBot methods."""
    return validate_livedub_audio_companion()
'''
text, count = re.subn(pattern, replacement, text, count=1, flags=re.S)
if count != 1:
    raise SystemExit(f"companion Telegram patch removal count={count}")
write(path, text)

# ---------------------------------------------------------------------------
# Mix source ownership: UTF-8 ffprobe, role-correct RU track, complete major QA
# windows and a postcondition on rebuilt media.
# ---------------------------------------------------------------------------
path = "services/livedub_mix.py"
text = read(path)
old = '''            capture_output=True, text=True, timeout=60,
        )
        data = _json.loads(proc.stdout or "{}")'''
new = '''            capture_output=True, text=True, timeout=60,
            encoding="utf-8", errors="replace",
        )
        data = _json.loads(proc.stdout or "{}")'''
if text.count(old) != 1:
    raise SystemExit(f"livedub_mix probe UTF-8 anchor count={text.count(old)}")
text = text.replace(old, new, 1)

pattern = r'def find_pro_tracks\(workdir: Path\) -> tuple\[Optional\[Path\], Optional\[Path\]\]:.*?\n    return orig, ru\n'
replacement = '''def find_pro_tracks(workdir: Path) -> tuple[Optional[Path], Optional[Path]]:
    """Return the source video plus the role-correct clean RU translation."""
    workdir = Path(workdir)
    orig = None
    for candidate in sorted(workdir.glob("original_video.*")):
        if has_video_stream(candidate):
            orig = candidate
            break
    from services.livedub_audio_quality_guard import select_clean_translation_mp3

    return orig, select_clean_translation_mp3(workdir)
'''
text, count = re.subn(pattern, replacement, text, count=1, flags=re.S)
if count != 1:
    raise SystemExit(f"livedub_mix find_pro_tracks replacement count={count}")

pattern = r'def extract_fix_intervals\(issues: list\[dict\], max_fixes: int = 6\) -> list\[tuple\[float, float\]\]:.*?\n    return merged\n'
replacement = '''def extract_fix_intervals(
    issues: list[dict],
    max_fixes: int | None = None,
) -> list[tuple[float, float]]:
    """Return merged windows covering every valid major QA timestamp.

    A positive explicit/environment limit fails closed instead of silently
    truncating the repair set. ``0``/unset means unlimited.
    """
    delay_s = get_mix_params()["delay_ms"] / 1000.0
    intervals: list[tuple[float, float]] = []
    for issue in issues or []:
        if not isinstance(issue, dict):
            continue
        if str(issue.get("severity") or "").strip().casefold() != "major":
            continue
        moment = parse_mmss(str(issue.get("time") or ""))
        if moment is None:
            continue
        intervals.append((
            max(0.0, moment - _FIX_PRE),
            moment - _FIX_PRE + _FIX_LEN + delay_s,
        ))

    intervals.sort()
    merged: list[tuple[float, float]] = []
    for start, end in intervals:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))

    configured = os.getenv("LIVEDUB_AUTOFIX_MAX_INTERVALS", "0").strip()
    try:
        env_limit = max(0, int(configured or "0"))
    except ValueError:
        env_limit = 0
    explicit_limit = max(0, int(max_fixes or 0))
    limit = explicit_limit or env_limit
    if limit and len(merged) > limit:
        raise RuntimeError(
            f"QA produced {len(merged)} independent major intervals, above "
            f"the configured safe limit {limit}; refusing a partial auto-fix"
        )
    return merged
'''
text, count = re.subn(pattern, replacement, text, count=1, flags=re.S)
if count != 1:
    raise SystemExit(f"livedub_mix QA interval replacement count={count}")

old = '''    result = await mix_tracks(orig_video, ru_audio, out,
                              ru_extra_expr=ru_expr, en_extra_expr=en_expr)
    if result:
        logger.info("[LiveDubMix] авто-правка: %d интервал(ов) приглушено", len(intervals))'''
new = '''    result = await mix_tracks(orig_video, ru_audio, out,
                              ru_extra_expr=ru_expr, en_extra_expr=en_expr)
    if result:
        expected_duration = await asyncio.to_thread(probe_media_duration, orig_video)
        actual_duration = await asyncio.to_thread(probe_media_duration, Path(result))
        tolerance = max(3.0, float(expected_duration or 0) * 0.015)
        if (
            not has_video_stream(Path(result))
            or not expected_duration
            or not actual_duration
            or abs(float(expected_duration) - float(actual_duration)) > tolerance
        ):
            try:
                Path(result).unlink(missing_ok=True)
            except OSError:
                pass
            raise RuntimeError(
                f"QA auto-fix postcondition failed: source={expected_duration}, "
                f"fixed={actual_duration}"
            )
        logger.info("[LiveDubMix] авто-правка: %d интервал(ов) приглушено", len(intervals))'''
if text.count(old) != 1:
    raise SystemExit(f"livedub_mix QA postcondition anchor count={text.count(old)}")
text = text.replace(old, new, 1)
write(path, text)

# Yandex fallback lookup applies the same clean-role rule directly.
path = "services/yandex_live_dub.py"
text = read(path)
anchor = '''    files = [
        p for p in directory.glob(pattern)'''
replacement = '''    if str(pattern).casefold() == "*.mp3":
        from services.livedub_audio_quality_guard import select_clean_translation_mp3

        return select_clean_translation_mp3(directory)

    files = [
        p for p in directory.glob(pattern)'''
if text.count(anchor) != 1:
    raise SystemExit(f"yandex clean lookup anchor count={text.count(anchor)}")
text = text.replace(anchor, replacement, 1)
write(path, text)

# yt-dlp accepts one runtime per --js-runtimes occurrence.
replace_once(
    "services/ffmpeg.py",
    '        args += ["--js-runtimes", ",".join(js_runtimes)]',
    '        for runtime in js_runtimes:\n            args += ["--js-runtimes", runtime]',
)

# ---------------------------------------------------------------------------
# Gemini shared config is the source owner for semantic/utility thinking.
# ---------------------------------------------------------------------------
path = "core/globals.py"
text = read(path)
anchor = '''def make_audio_config(temperature: float = 0.1, max_output_tokens: int = 65536, model_name: str = None, thinking_level: str = "high", response_mime_type: str | None = None, response_schema=None):'''
helper = '''def _effective_thinking_level(model_name: str, requested: str) -> str:
    """Enforce the production semantic/utility split at the config owner."""
    model = str(model_name or "").strip().casefold()
    if model == "gemini-3.6-flash":
        return "high"
    if model in {"gemini-3.5-flash-lite", "gemini-3.5-flash"}:
        return "minimal"
    return str(requested or "high").strip().lower() or "high"


''' + anchor
if text.count(anchor) != 1:
    raise SystemExit("core.globals make_audio_config anchor mismatch")
text = text.replace(anchor, helper, 1)
old = '''    is_3x = _is_gemini_3x(model_name)
    # FIX 2026-05-21 #12 P2: gemini-3.5-flash поддерживает 65k output tokens [Google I/O 2026].'''
new = '''    is_3x = _is_gemini_3x(model_name)
    thinking_level = _effective_thinking_level(model_name, thinking_level)
    # FIX 2026-05-21 #12 P2: gemini-3.5-flash поддерживает 65k output tokens [Google I/O 2026].'''
if text.count(old) != 1:
    raise SystemExit(f"core.globals audio thinking anchor count={text.count(old)}")
text = text.replace(old, new, 1)
old = '''    is_3x = _is_gemini_3x(model_name)
    # FIX 2026-05-21 #12 P2: cap 65k для 3.x (см. выше в make_audio_config).'''
new = '''    is_3x = _is_gemini_3x(model_name)
    thinking_level = _effective_thinking_level(model_name, thinking_level)
    # FIX 2026-05-21 #12 P2: cap 65k для 3.x (см. выше в make_audio_config).'''
if text.count(old) != 1:
    raise SystemExit(f"core.globals text thinking anchor count={text.count(old)}")
text = text.replace(old, new, 1)
pattern = r'def make_text_config\(temperature: float = 0\.2, max_output_tokens: int = 14000\):.*?\n\n\n# FIX #2:'
replacement = '''def make_text_config(temperature: float = 0.2, max_output_tokens: int = 14000):
    """Legacy semantic helper routed through the source-owned smart config."""
    model_name = os.getenv("GEMINI_MODEL", "gemini-3.6-flash").strip() or "gemini-3.6-flash"
    return make_text_config_smart(
        temperature=temperature,
        max_output_tokens=max_output_tokens,
        model_name=model_name,
        thinking_level="high",
    )


# FIX #2:'''
text, count = re.subn(pattern, replacement, text, count=1, flags=re.S)
if count != 1:
    raise SystemExit(f"core.globals legacy text config replacement count={count}")
write(path, text)

# English subtitle translation is user-visible semantic text: 3.6/HIGH and no
# explicit Gemini-3.x sampling parameter.
path = "services/eng_subtitles.py"
text = read(path)
old = "from core.globals import GEMINI_CLIENTS"
if text.count(old) != 1:
    raise SystemExit("eng_subtitles import anchor mismatch")
text = text.replace(old, "from core.globals import GEMINI_CLIENTS, make_text_config_smart", 1)
pattern = r'            from google\.genai import types\n            response = await asyncio\.wait_for\(\n                client\.aio\.models\.generate_content\(\n                    model=GEMINI_MODEL,\n                    contents=prompt,\n                    config=types\.GenerateContentConfig\(\n                        temperature=0\.2,\n                        response_mime_type="application/json",\n                    \)\n                \),'
replacement = '''            response = await asyncio.wait_for(
                client.aio.models.generate_content(
                    model=GEMINI_MODEL,
                    contents=prompt,
                    config=make_text_config_smart(
                        max_output_tokens=16000,
                        model_name=GEMINI_MODEL,
                        thinking_level="high",
                        response_mime_type="application/json",
                    ),
                ),'''
text, count = re.subn(pattern, replacement, text, count=1)
if count != 1:
    raise SystemExit(f"eng_subtitles Gemini config replacement count={count}")
write(path, text)

# Historical installer names remain callable only as validators; no post-import
# sys.modules/reference rewriting or LiveDub probe/function replacement.
path = "services/gemini_max_quality.py"
text = read(path)
pattern = r'def install_max_quality_runtime\(\) -> None:.*\Z'
replacement = '''def install_max_quality_runtime() -> str:
    """Compatibility validator; quality is enforced by config owners/pre-main env."""
    import core.globals as globals_module

    if not callable(globals_module.make_text_config_smart):
        raise RuntimeError("Gemini smart config owner is unavailable")
    return "source-owned Gemini thinking policy; no post-import reference replacement"
'''
text, count = re.subn(pattern, replacement, text, count=1, flags=re.S)
if count != 1:
    raise SystemExit(f"gemini_max_quality installer neutralization count={count}")
write(path, text)

path = "services/livedub_quality_runtime.py"
text = read(path)
pattern = r'def install_livedub_quality_runtime\(\) -> None:.*\Z'
replacement = '''def install_livedub_quality_runtime() -> str:
    """Compatibility validator; delivery/probe ownership is now explicit."""
    _install_quality_models()
    return (
        "semantic=Gemini 3.6/HIGH/no-fallback; explicit LiveDub coordinator; "
        "source-owned UTF-8 probes"
    )
'''
text, count = re.subn(pattern, replacement, text, count=1, flags=re.S)
if count != 1:
    raise SystemExit(f"livedub_quality_runtime installer neutralization count={count}")
write(path, text)

# ---------------------------------------------------------------------------
# Main pipeline explicit delivery/publication orchestration.
# ---------------------------------------------------------------------------
path = "pipelines/main_pipeline.py"
text = read(path)
anchor = '''        _livedub_failure_notice_sent = False
        _livedub_result_sent = False
        _livedub_video_for_downstream = None
'''
replacement = anchor + '''
        from services.livedub_delivery_coordinator import create_source_audio_deferral

        _source_audio_delivery = create_source_audio_deferral(
            bot=context.bot if context else None,
            chat_id=update.effective_chat.id if context else None,
            reply_to=update.message.message_id if context else None,
            enabled=bool(
                context
                and user_mode == "eng"
                and (_livedub_cached_file_id or live_dub_task)
            ),
        )
'''
if text.count(anchor) != 1:
    raise SystemExit(f"pipeline source deferral anchor count={text.count(anchor)}")
text = text.replace(anchor, replacement, 1)

pattern = r'            _livedub_title_line = ""\n.*?\n            # Мгновенная повторная отправка по кэшированному file_id'
replacement = '''            from services.livedub_publication import (
                build_publication_card,
                format_video_caption,
            )

            _publication_ai = None
            try:
                _publication_ai = ai_data if isinstance(ai_data, dict) else None
            except NameError:
                _publication_ai = None
            if _publication_ai is None:
                try:
                    _publication_ai = c_ai if isinstance(c_ai, dict) else None
                except NameError:
                    _publication_ai = None

            _publication_title = str((_publication_ai or {}).get("real_title") or title or "").strip()
            _publication_author = str((_publication_ai or {}).get("real_author") or performer or "").strip()
            _publication_source_line = (
                f"{_publication_title} - {_publication_author}"
                if _publication_author
                else (_publication_title or full_title or "Переведённое видео")
            )
            _publication_card = await build_publication_card(_publication_source_line, url)
            _pub_title = str(_publication_card.get("title") or _publication_title or "Переведённое видео").strip()
            _pub_author = str(_publication_card.get("author") or _publication_author or "").strip()
            _livedub_title_line = (
                f"{_pub_title} - {_pub_author}"
                if _pub_author and _pub_author.casefold() not in _pub_title.casefold()
                else _pub_title
            )
            _livedub_title_html = html_mod.escape(_livedub_title_line) if _livedub_title_line else ""

            # Мгновенная повторная отправка по кэшированному file_id'''
text, count = re.subn(pattern, replacement, text, count=1, flags=re.S)
if count != 1:
    raise SystemExit(f"pipeline publication owner replacement count={count}")

pattern = r'            if _livedub_cached_file_id and context:\n.*?\n            if not live_dub_task:'
replacement = '''            if _livedub_cached_file_id and context:
                from services.livedub_delivery_coordinator import (
                    delete_message_best_effort,
                    deliver_cached_companions,
                )

                _cached_internal_caption = (
                    f"<b>{_livedub_title_html}</b>\n🎬 Живые голоса Яндекса"
                    if _livedub_title_html else "🎬 Живые голоса Яндекса"
                )
                _cached_cap = format_video_caption(_publication_card, _cached_internal_caption)
                _cached_video_message = None
                try:
                    _cached_video_message = await context.bot.send_video(
                        chat_id=update.effective_chat.id,
                        video=_livedub_cached_file_id,
                        caption=_cached_cap,
                        parse_mode="HTML",
                        reply_to_message_id=update.message.message_id,
                        supports_streaming=True,
                    )
                    companions_ok = await deliver_cached_companions(
                        context.bot,
                        chat_id=update.effective_chat.id,
                        video_file_id=_livedub_cached_file_id,
                        publication_card=_publication_card,
                        reply_to=update.message.message_id,
                    )
                    if not companions_ok:
                        raise RuntimeError("cached LiveDub companion set is missing")
                except Exception as _fid_err:
                    if _cached_video_message is not None:
                        await delete_message_best_effort(
                            context.bot, update.effective_chat.id, _cached_video_message
                        )
                    logger.warning("[LiveDub] cached pair rejected: %s", _fid_err)
                    try:
                        from core.database import adb_set_livedub_file_id
                        await adb_set_livedub_file_id(media_id, "")
                    except Exception:
                        pass
                    await _source_audio_delivery.flush("cached LiveDub pair unavailable")
                    try:
                        await context.bot.send_message(
                            chat_id=update.effective_chat.id,
                            text=(
                                "⚠️ Кэшированный перевод или его MP3-комплект устарел. "
                                "Отправьте ссылку ещё раз — полный комплект будет создан заново."
                            ),
                            reply_to_message_id=update.message.message_id,
                        )
                    except Exception:
                        pass
                    _livedub_result_sent = True
                    return True

                _source_audio_delivery.discard("cached LiveDub pair delivered")
                _livedub_result_sent = True
                return True
            if not live_dub_task:'''
text, count = re.subn(pattern, replacement, text, count=1, flags=re.S)
if count != 1:
    raise SystemExit(f"pipeline cached LiveDub replacement count={count}")

anchor = '''                # Path вместо file handle: с локальным Bot API (local_mode=True)'''
insert = '''                if not is_fallback:
                    caption = format_video_caption(_publication_card, caption)

''' + anchor
if text.count(anchor) != 1:
    raise SystemExit(f"pipeline video caption anchor count={text.count(anchor)}")
text = text.replace(anchor, insert, 1)

pattern = r'                # Сохраняем file_id для мгновенной повторной отправки\n                if not is_fallback:\n.*?\n\n                # В Quick QA'
replacement = '''                # Companion delivery is an explicit transaction. Only a complete
                # video+MP3 result enters the fast-resend video cache.
                _video_file_id = str(
                    getattr(getattr(_sent_msg, "video", None), "file_id", "") or ""
                ).strip()
                if is_fallback:
                    await _source_audio_delivery.flush("LiveDub fell back to original video")
                else:
                    from services.livedub_delivery_coordinator import deliver_new_companions

                    _companions_ok = False
                    try:
                        _companions_ok = await deliver_new_companions(
                            context.bot,
                            chat_id=update.effective_chat.id,
                            video_path=Path(livedub_path),
                            publication_card=_publication_card,
                            reply_to=update.message.message_id,
                            thumbnail=_v_thumb,
                            video_file_id=_video_file_id,
                        )
                    except Exception as _companion_err:
                        logger.exception(
                            "[LiveDub] companion transaction failed: %s",
                            str(_companion_err)[:180],
                        )
                        await _source_audio_delivery.flush("LiveDub companion transaction failed")
                        try:
                            await context.bot.send_message(
                                chat_id=update.effective_chat.id,
                                text=(
                                    "⚠️ Видео с переводом отправлено, но полный проверенный "
                                    "MP3-комплект сформировать не удалось. Видео не сохранено "
                                    "в быстрый кэш как полный комплект."
                                ),
                                reply_to_message_id=update.message.message_id,
                            )
                        except Exception:
                            pass

                    if _companions_ok:
                        _source_audio_delivery.discard("complete LiveDub video+MP3 set delivered")
                        if _video_file_id:
                            try:
                                from core.database import adb_set_livedub_file_id
                                await adb_set_livedub_file_id(media_id, _video_file_id)
                                logger.info("[LiveDub] complete pair file_id cached (%s)", media_id)
                            except Exception as _fid_save_err:
                                logger.warning("[LiveDub] complete pair cache save failed: %s", _fid_save_err)
                                try:
                                    import services.livedub_audio_companion as _companion_cache
                                    _companion_cache._cache_drop(_video_file_id)
                                except Exception:
                                    pass

                # В Quick QA'''
text, count = re.subn(pattern, replacement, text, count=1, flags=re.S)
if count != 1:
    raise SystemExit(f"pipeline new companion replacement count={count}")

old = '''                    _livedub_result_sent = True
                    return True
                _title_prefix ='''
new = '''                    await _source_audio_delivery.flush("LiveDub video exceeds Telegram limit")
                    _livedub_result_sent = True
                    return True
                _title_prefix ='''
if text.count(old) != 1:
    raise SystemExit(f"pipeline oversize flush anchor count={text.count(old)}")
text = text.replace(old, new, 1)

old = '''            except asyncio.TimeoutError:
                # wait_for отменяет задачу при таймауте — перевод уже не придёт.'''
new = '''            except asyncio.TimeoutError:
                await _source_audio_delivery.flush("LiveDub timeout")
                # wait_for отменяет задачу при таймауте — перевод уже не придёт.'''
if text.count(old) != 1:
    raise SystemExit("pipeline timeout flush anchor mismatch")
text = text.replace(old, new, 1)

old = '''            except Exception as e:
                logger.warning(f"[LiveDub] fail: {e}")'''
new = '''            except Exception as e:
                await _source_audio_delivery.flush("LiveDub send/generation failure")
                logger.warning(f"[LiveDub] fail: {e}")'''
if text.count(old) != 1:
    raise SystemExit("pipeline failure flush anchor mismatch")
text = text.replace(old, new, 1)

old = '''                _livedub_failure_notice_sent = True
                reason = str(reason or "").strip()'''
new = '''                _livedub_failure_notice_sent = True
                await _source_audio_delivery.flush("LiveDub unavailable")
                reason = str(reason or "").strip()'''
if text.count(old) != 1:
    raise SystemExit("pipeline failure notice flush anchor mismatch")
text = text.replace(old, new, 1)

old = '''            if _audio_fid:
                try:
                    await update.message.reply_audio('''
new = '''            if _audio_fid and not _source_audio_delivery.enabled:
                try:
                    await update.message.reply_audio('''
if text.count(old) != 1:
    raise SystemExit(f"pipeline cached source file_id anchor count={text.count(old)}")
text = text.replace(old, new, 1)

old = '''                        _sent_audio_msg = await update.message.reply_audio(
                            audio=af, title=_title_c, performer=_performer_c,'''
new = '''                        _sent_audio_msg = await _source_audio_delivery.send_or_defer(
                            update.message,
                            audio=af, fallback_path=mp3_path,
                            title=_title_c, performer=_performer_c,'''
if text.count(old) != 1:
    raise SystemExit(f"pipeline cached local source deferral count={text.count(old)}")
text = text.replace(old, new, 1)

old = '''                    _sent_fresh_audio = await update.message.reply_audio(
                        audio=audio_file, title=audio_title, performer=audio_performer,'''
new = '''                    _sent_fresh_audio = await _source_audio_delivery.send_or_defer(
                        update.message,
                        audio=audio_file, fallback_path=mp3_path,
                        title=audio_title, performer=audio_performer,'''
if text.count(old) != 1:
    raise SystemExit(f"pipeline fresh source deferral count={text.count(old)}")
text = text.replace(old, new, 1)

old = '''        await _send_livedub_result()
        cleanup_files(media_id)'''
new = '''        await _send_livedub_result()
        if _source_audio_delivery.has_pending:
            await _source_audio_delivery.flush("pipeline completed without a complete LiveDub pair")
        cleanup_files(media_id)'''
if text.count(old) != 1:
    raise SystemExit(f"pipeline final source fallback anchor count={text.count(old)}")
text = text.replace(old, new, 1)
write(path, text)

# ---------------------------------------------------------------------------
# Durable audit + architecture regressions.
# ---------------------------------------------------------------------------
audit = Path("docs/quality_audits/2026-08-15-explicit-livedub-delivery.md")
audit.parent.mkdir(parents=True, exist_ok=True)
audit.write_text(
    '''# 2026-08-15 — Explicit LiveDub delivery / no Telegram monkey-patch

## Problem

Production LiveDub delivery had accumulated a stack of runtime adapters that
replaced `Bot.send_video`, `Bot.send_audio`, `Bot.send_message`,
`Message.reply_audio`, private companion functions and used a `sys.meta_path`
import hook. The behavior was heavily tested, but ownership depended on import
order and wrapper order.

## Refactor

- `services` package import is side-effect free; `runtime_manifest` is the only
  startup lifecycle owner.
- Gemini/network/quality policy is an explicit PRE_MAIN feature.
- LiveDub video/MP3 publication is called explicitly from `main_pipeline`.
- New and cached companion MP3 sets are transactions in
  `livedub_delivery_coordinator`: strict clean/mixed role validation, rollback,
  verified file IDs and request-local single-flight.
- ENG Full source MP3 fallback is a request-scoped `SourceAudioDeferral`, not a
  global `Message.reply_audio` interceptor.
- Companion cache corruption recovery is a directly called persistence backend.
- Clean RU selection, Windows UTF-8 probing, QA major-interval coverage and
  yt-dlp runtime argument shape are source-owned.
- Shared Gemini config enforces 3.6/HIGH semantic and 3.5 utility thinking at the
  config owner; no post-import `sys.modules` reference rewrite is required.

## Quality invariants

No semantic downgrade was introduced: user-visible Gemini remains exact
`gemini-3.6-flash`/HIGH with no 3.5 semantic fallback; Whisper stays `large-v3`;
Factory score/boundary/render contracts are untouched. A new LiveDub video stays
visible if companion delivery fails, but it is not cached as a complete pair. A
stale cached video is rolled back when its companion set cannot be proven.

Exact-head full repository CI, Windows full-suite and `tools/verify_repo.py` are
required before merge.
''',
    encoding="utf-8",
)

test = Path("tests/test_explicit_livedub_delivery_architecture.py")
test.write_text(
    '''from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _source(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_services_import_has_no_import_hook_or_install_side_effects():
    src = _source("services/__init__.py")
    assert "sys.meta_path" not in src
    assert "MetaPathFinder" not in src
    assert "install_" not in src


def test_manifest_uses_explicit_delivery_contract_not_telegram_patch_stack():
    src = _source("services/runtime_manifest.py")
    for feature in (
        "livedub-audio-companion",
        "livedub-audio-dedupe",
        "livedub-new-delivery-atomicity",
        "livedub-cached-delivery-atomicity",
        "livedub-deep-audit",
        "livedub-dual-audio-policy",
        "livedub-output-policy",
    ):
        assert f'"{feature}"' not in src
    assert '"livedub-delivery-contract"' in src
    assert '"pre-main-quality-policy"' in src


def test_pipeline_calls_explicit_delivery_transactions():
    src = _source("pipelines/main_pipeline.py")
    assert "deliver_new_companions(" in src
    assert "deliver_cached_companions(" in src
    assert "create_source_audio_deferral(" in src
    assert "format_video_caption(_publication_card" in src


def test_coordinator_does_not_patch_telegram_methods():
    tree = ast.parse(_source("services/livedub_delivery_coordinator.py"))
    forbidden = {"send_video", "send_audio", "send_message", "reply_audio"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "setattr":
            if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
                assert node.args[1].value not in forbidden
    assert "sys.meta_path" not in _source("services/livedub_delivery_coordinator.py")


def test_companion_cache_and_clean_track_are_source_owned():
    companion = _source("services/livedub_audio_companion.py")
    assert "load_recoverable_cache(_cache_path())" in companion
    assert "save_recoverable_cache(_cache_path(), data)" in companion
    assert 'setattr(cls, "send_video"' not in companion
    quality = _source("services/livedub_audio_quality_guard.py")
    assert "mix.find_pro_tracks =" not in quality
    assert "companion._send_new_audio =" not in quality


def test_livedub_mix_probe_is_utf8_and_major_fix_does_not_truncate():
    src = _source("services/livedub_mix.py")
    probe = src[src.index("def probe_video_meta"):src.index("def make_video_thumbnail")]
    assert 'encoding="utf-8"' in probe
    assert 'errors="replace"' in probe
    fix = src[src.index("def extract_fix_intervals"):src.index("async def apply_qa_audio_fixes")]
    assert "break" not in fix
    assert "refusing a partial auto-fix" in fix


def test_gemini_semantic_config_is_source_owned_high_and_sampling_free():
    src = _source("core/globals.py")
    assert 'if model == "gemini-3.6-flash":' in src
    assert 'return "high"' in src
    legacy = src[src.index("def make_text_config("):src.index("# FIX #2:")]
    assert "make_text_config_smart(" in legacy
    assert "GenerateContentConfig(" not in legacy
    subtitles = _source("services/eng_subtitles.py")
    assert 'thinking_level="high"' in subtitles
    assert "temperature=0.2" not in subtitles
''',
    encoding="utf-8",
)

# Self-delete implementation machinery before the branch commit.
Path(".github/workflows/refactor-explicit-livedub-once.yml").unlink(missing_ok=True)
Path(__file__).unlink(missing_ok=True)
