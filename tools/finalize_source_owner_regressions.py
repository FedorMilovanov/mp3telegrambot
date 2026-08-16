#!/usr/bin/env python3
from __future__ import annotations

import ast
import subprocess
from pathlib import Path


def git_show(path: str) -> str:
    return subprocess.check_output(
        ["git", "show", f"origin/main:{path}"],
        text=True,
        encoding="utf-8",
    )


def write(path: str, text: str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text.rstrip() + "\n", encoding="utf-8")


def remove_functions(path: str, names: set[str]) -> None:
    target = Path(path)
    tree = ast.parse(target.read_text(encoding="utf-8"))
    tree.body = [
        node
        for node in tree.body
        if not (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name in names
        )
    ]
    ast.fix_missing_locations(tree)
    target.write_text(ast.unparse(tree).rstrip() + "\n", encoding="utf-8")


# LiveDub evidence: preserve deterministic anti-hallucination policy, drop installer.
guard = git_show("services/livedub_info_guard.py")
guard = guard.split("\ndef install_livedub_info_guard()", 1)[0]
guard = guard.replace(
    "Deterministic grounding for lightweight LiveDub publication cards.",
    "Pure deterministic grounding policy for LiveDub publication cards.",
    1,
)
write("services/livedub_info_evidence.py", guard)

# LiveDub presentation: keep pure helpers, drop the module-mutation installer.
presentation = git_show("services/livedub_info_presentation.py")
presentation = presentation.split("\ndef install_livedub_info_presentation()", 1)[0]
presentation = presentation.replace("import threading\n", "")
presentation = presentation.replace("_INSTALL_LOCK = threading.Lock()\n", "")
presentation += r'''

async def apply_card_presentation(
    card: dict | None,
    title_line: str,
    *,
    source_url: str = "",
) -> dict:
    """Apply concise Russian publication presentation without module mutation."""
    result = dict(card or {})
    current_line = _clean(result.get("youtube_title") or title_line, 260)
    translated = None
    if current_line and not _title_has_cyrillic(current_line):
        translated = await _translate_title_second_chance(current_line)
    if translated:
        translated_title, translated_author = translated
        result["youtube_title"] = (
            f"{translated_title} - {translated_author}"
            if translated_author else translated_title
        )
    elif not result.get("youtube_title"):
        result["youtube_title"] = _clean(title_line, 220)

    final_title, final_author = _split_title_author(
        _clean(result.get("youtube_title") or title_line, 260)
    )
    description = _clean(result.get("telegram_description"), 700)
    if (
        not description
        or description.casefold() in {
            _clean(title_line, 260).casefold(),
            final_title.casefold(),
        }
        or not re.search(r"[А-Яа-яЁё]", description)
    ):
        result["telegram_description"] = _fallback_description(
            final_title,
            final_author,
        )
    if source_url:
        result["source_url"] = source_url
    return result


def format_card_message(card: dict) -> str:
    """Render the concise publication card through the pure formatter."""
    from converters.md_telegraph import safe_trim_caption

    class _FormattingOwner:
        pass

    owner = _FormattingOwner()
    owner.safe_trim_caption = safe_trim_caption
    return _make_formatter(owner)(card)
'''
write("services/livedub_info_presentation_policy.py", presentation)

info_path = Path("services/livedub_info.py")
info = info_path.read_text(encoding="utf-8")
old_import = (
    "from converters.md_telegraph import safe_trim_caption\n"
    "from services.livedub_qa import srt_to_timed_text\n"
)
new_import = (
    "from services import livedub_info_presentation_policy as presentation_policy\n"
    "from services.livedub_info_evidence import (\n"
    "    full_srt_evidence,\n"
    "    sampled_srt_to_timed_text,\n"
    "    sanitize_card,\n"
    ")\n"
)
if old_import not in info:
    raise SystemExit("livedub_info import anchor missing")
info = info.replace(old_import, new_import, 1)

snapshot_anchor = '''def _gemini_clients_snapshot() -> tuple[Any, ...]:
    """Return a request-local client order without mutating the shared registry."""
    return tuple(GEMINI_CLIENTS)
'''
finalizer = snapshot_anchor + '''

async def _finalize_info_card(
    card: dict | None,
    *,
    title_line: str,
    source_url: str,
    evidence: str,
) -> dict:
    guarded = sanitize_card(card, str(title_line or ""), evidence)
    return await presentation_policy.apply_card_presentation(
        guarded,
        str(title_line or ""),
        source_url=source_url,
    )
'''
if snapshot_anchor not in info:
    raise SystemExit("livedub_info snapshot anchor missing")
info = info.replace(snapshot_anchor, finalizer, 1)

old_srt = '''    timed_text = ""
    try:
        if dub_srt_path and Path(dub_srt_path).exists():
            timed_text = srt_to_timed_text(Path(dub_srt_path), max_chars=7000)
    except Exception as exc:
        logger.info("[LiveDubInfo] SRT read failed: %s", str(exc)[:120])
        timed_text = ""
'''
new_srt = '''    timed_text = ""
    evidence = ""
    try:
        if dub_srt_path and Path(dub_srt_path).exists():
            srt_path = Path(dub_srt_path)
            timed_text = sampled_srt_to_timed_text(srt_path, max_chars=7000)
            evidence = full_srt_evidence(srt_path)
    except Exception as exc:
        logger.info("[LiveDubInfo] SRT read failed: %s", str(exc)[:120])
        timed_text = ""
        evidence = ""
'''
if old_srt not in info:
    raise SystemExit("livedub_info SRT anchor missing")
info = info.replace(old_srt, new_srt, 1)

no_clients = '''    clients = _gemini_clients_snapshot()
    if not clients:
        return fallback
'''
no_clients_new = '''    clients = _gemini_clients_snapshot()
    if not clients:
        return await _finalize_info_card(
            fallback,
            title_line=title_line,
            source_url=source_url,
            evidence=evidence,
        )
'''
if no_clients not in info:
    raise SystemExit("livedub_info no-client anchor missing")
info = info.replace(no_clients, no_clients_new, 1)

success = '''                card = _normalize_card(data, title_line, source_url)
                card["model"] = model
                return card
'''
success_new = '''                card = _normalize_card(data, title_line, source_url)
                card["model"] = model
                return await _finalize_info_card(
                    card,
                    title_line=title_line,
                    source_url=source_url,
                    evidence=evidence,
                )
'''
if success not in info:
    raise SystemExit("livedub_info success anchor missing")
info = info.replace(success, success_new, 1)

final_fallback = '''    return fallback


def _h(text: Any) -> str:
'''
final_fallback_new = '''    return await _finalize_info_card(
        fallback,
        title_line=title_line,
        source_url=source_url,
        evidence=evidence,
    )


def _h(text: Any) -> str:
'''
if final_fallback not in info:
    raise SystemExit("livedub_info final fallback anchor missing")
info = info.replace(final_fallback, final_fallback_new, 1)

formatter_start = info.index("def format_livedub_info_message(card: dict) -> str:")
info = info[:formatter_start] + '''def format_livedub_info_message(card: dict) -> str:
    """Render the concise source-owned LiveDub publication card."""
    return presentation_policy.format_card_message(card)
'''
info_path.write_text(info.rstrip() + "\n", encoding="utf-8")

# Direct identity QA: extract pure fail-closed logic from retired installer.
mono = git_show("tools/voxcpm2/monolithic_runtime_install.py")
mono_tree = ast.parse(mono)
wanted_constants = {
    "FAIL_CLOSED_IDENTITY_POLICY",
    "ABSOLUTE_GLOBAL_F0_LIMIT_ST",
    "ABSOLUTE_ADJACENT_F0_RATIO",
    "ABSOLUTE_ADJACENT_P90_RATIO",
}
wanted_functions = {
    "_finite",
    "_ratio",
    "_semitones",
    "_append_failure",
    "enforce_fail_closed_identity",
}
chunks: list[str] = []
for node in mono_tree.body:
    if isinstance(node, ast.Assign):
        names = {t.id for t in node.targets if isinstance(t, ast.Name)}
        if names & wanted_constants:
            chunks.append(ast.get_source_segment(mono, node) or ast.unparse(node))
    elif isinstance(node, ast.FunctionDef) and node.name in wanted_functions:
        chunks.append(ast.get_source_segment(mono, node) or ast.unparse(node))

identity = '''#!/usr/bin/env python3
"""Pure fail-closed speaker identity policy for assembled Russian audio."""
from __future__ import annotations

import math
from typing import Any

''' + "\n\n".join(chunks) + '''

__all__ = [
    "ABSOLUTE_ADJACENT_F0_RATIO",
    "ABSOLUTE_ADJACENT_P90_RATIO",
    "ABSOLUTE_GLOBAL_F0_LIMIT_ST",
    "FAIL_CLOSED_IDENTITY_POLICY",
    "enforce_fail_closed_identity",
]
'''
write("tools/voxcpm2/direct_fail_closed_identity.py", identity)

timeline_path = Path("tools/voxcpm2/direct_timeline_delivery_qa.py")
timeline = timeline_path.read_text(encoding="utf-8")
import_anchor = "from tools.voxcpm2 import direct_source_relative_continuity\n"
if import_anchor not in timeline:
    raise SystemExit("timeline import anchor missing")
timeline = timeline.replace(
    import_anchor,
    import_anchor + "from tools.voxcpm2 import direct_fail_closed_identity\n",
    1,
)
timeline = timeline.replace(
    "def _sequence_checks(rows: list[dict[str, Any]]) -> dict[str, float]:",
    "def _sequence_checks(rows: list[dict[str, Any]]) -> dict[str, Any]:",
    1,
)
return_anchor = '''    return {
        "baseline_f0_median": baseline_f0,
        "source_baseline_f0_median": source_baseline_f0,
        "baseline_spectral_centroid_hz": baseline_centroid,
    }
'''
return_new = '''    direct_fail_closed_identity.enforce_fail_closed_identity(
        rows,
        baseline_f0=baseline_f0,
    )
    return {
        "baseline_f0_median": baseline_f0,
        "source_baseline_f0_median": source_baseline_f0,
        "baseline_spectral_centroid_hz": baseline_centroid,
        "fail_closed_identity_policy": direct_fail_closed_identity.FAIL_CLOSED_IDENTITY_POLICY,
    }
'''
if return_anchor not in timeline:
    raise SystemExit("timeline sequence return anchor missing")
timeline = timeline.replace(return_anchor, return_new, 1)
timeline_path.write_text(timeline, encoding="utf-8")

# Retarget useful tests to canonical owners.
p = Path("tests/test_livedub_info_guard.py")
p.write_text(
    p.read_text(encoding="utf-8").replace(
        "from services.livedub_info_guard import sampled_srt_to_timed_text, sanitize_card",
        "from services.livedub_info_evidence import sampled_srt_to_timed_text, sanitize_card",
    ),
    encoding="utf-8",
)

p = Path("tests/test_cross_language_identity_fail_closed.py")
p.write_text(
    p.read_text(encoding="utf-8").replace(
        "from tools.voxcpm2 import monolithic_runtime_install",
        "from tools.voxcpm2 import direct_fail_closed_identity as monolithic_runtime_install",
    ),
    encoding="utf-8",
)

for filename in (
    "tests/test_generic_project_runtime.py",
    "tests/test_dub_runtime_regressions.py",
):
    p = Path(filename)
    p.write_text(
        p.read_text(encoding="utf-8").replace(
            "from tools.voxcpm2.generic_short_runtime import standardize_russian_title",
            "from tools.voxcpm2.generic_short_production import standardize_russian_title",
        ),
        encoding="utf-8",
    )

p = Path("tests/test_direct_tempo_boundary_resume.py")
p.write_text(
    p.read_text(encoding="utf-8").replace(
        "from tools.voxcpm2 import generic_clean_direct_runtime as direct_runtime",
        "from tools.voxcpm2 import generic_direct_runtime as direct_runtime",
    ),
    encoding="utf-8",
)

p = Path("tests/test_gemini_translation_quality.py")
text = p.read_text(encoding="utf-8")
text = text.replace(
    "from tools.voxcpm2 import generic_short_runtime",
    "from tools.voxcpm2 import generic_short_production as generic_short_runtime",
)
text = text.replace(
    'RUNTIME = ROOT / "tools" / "voxcpm2" / "generic_short_runtime.py"',
    'RUNTIME = ROOT / "tools" / "voxcpm2" / "generic_short_production.py"',
)
text = text.replace(
    'GEMINI_ENTRY = ROOT / "tools" / "voxcpm2" / "generic_clean_gemini_runtime.py"\n',
    "",
)
text = text.replace(
    'runtime.index("def install_runtime_adapters")',
    'runtime.index("def validate_translation")',
)
text = text.replace(
    'generic_short_runtime.pipeline, "log"',
    'generic_short_runtime, "log"',
)
p.write_text(text, encoding="utf-8")
remove_functions(
    str(p),
    {"test_clean_gemini_route_uses_expressive_translator_and_key_pool"},
)

p = Path("tests/test_clean_request_settings.py")
p.write_text(
    p.read_text(encoding="utf-8").replace(
        "from tools.voxcpm2 import generic_clean_direct_runtime as direct\n",
        "",
    ),
    encoding="utf-8",
)
remove_functions(
    str(p),
    {
        "test_direct_clean_wrapper_ignores_legacy_or_default",
        "test_all_clean_routes_repair_manifest_and_override_delay",
    },
)

p = Path("tests/test_direct_renderer_failure_resume.py")
p.write_text(
    p.read_text(encoding="utf-8").replace(
        "from tools.voxcpm2 import generic_clean_direct_runtime as clean_direct\n",
        "",
    ),
    encoding="utf-8",
)
remove_functions(
    str(p),
    {"test_ready_srt_runtime_owns_signature_based_resume_contract"},
)

p = Path("tests/test_factory_capacity_fast_fail.py")
text = p.read_text(encoding="utf-8")
text = text.replace("import sys\n", "")
text = text.replace(
    "from types import ModuleType, SimpleNamespace",
    "from types import SimpleNamespace",
)
text = text.replace("from services import livedub_info_presentation\n", "")
p.write_text(text, encoding="utf-8")
remove_functions(
    str(p),
    {"test_livedub_presentation_preserves_native_all_clients_marker"},
)

# These files assert removed mutation mechanisms themselves, not user behavior.
for filename in (
    "tests/test_cloud_media_fallback.py",
    "tests/test_cut_replay_delivery_policy.py",
    "tests/test_dialogue_suppressed_master.py",
):
    Path(filename).unlink(missing_ok=True)
