from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

CRITICAL_SURFACES = (
    "services/__init__.py",
    "services/runtime_manifest.py",
    "services/pre_main_policy.py",
    "services/gemini_max_quality.py",
    "services/polling_reliability_runtime.py",
    "services/restart_state_runtime.py",
    "services/bot_lifecycle.py",
    "main.py",
    "services/livedub_quality_runtime.py",
    "services/livedub_delivery_coordinator.py",
    "services/livedub_audio_companion.py",
    "services/livedub_audio_cache_recovery.py",
    "services/livedub_audio_quality_guard.py",
    "services/livedub_qa.py",
    "services/livedub_long_qa.py",
    "services/livedub_qa_trust.py",
    "services/livedub_qa_hardening.py",
    "services/livedub_ru_provenance.py",
    "services/study_synthesis_runtime.py",
    "services/study_synthesis_policy.py",
    "services/conspect_quality_contract.py",
    "services/conspect_audit_runtime.py",
    "core/study_quality.py",
    "core/structured_blocks.py",
    "core/content_audit.py",
    "pipelines/main_pipeline.py",
    "tools/voxcpm2/clean_production_core.py",
)

PRODUCTION_ENTRYPOINTS = (
    "bot.py",
    "bot_new.py",
    "main.py",
)

PRODUCTION_DIRS = (
    "core",
    "handlers",
    "pipelines",
    "services",
    "tools/voxcpm2",
)

FORBIDDEN_TEXT = (
    "sys.meta_path.insert",
    "sys.meta_path.remove",
    "MetaPathFinder",
    "Bot.send_video =",
    "Bot.send_audio =",
    "Bot.send_message =",
    "ExtBot.send_video =",
    "Message.reply_audio =",
    "Updater.start_polling =",
    "Application.process_update =",
    "yandex.get_live_dub_audio =",
    "mix.find_pro_tracks =",
    ".run_translation_qa =",
    ".format_qa_report =",
    ".normalize_structured_block =",
    ".audit_expanded_sections =",
    ".STUDY_ANALYSIS_PROMPT =",
    "_SubprocessProxy",
    "subprocess = _SubprocessProxy()",
)

TELEGRAM_METHODS = {"send_video", "send_audio", "send_message", "reply_audio"}
SYS_MODULE_MUTATORS = {"setdefault", "update", "__setitem__"}


def _source(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _production_python_paths() -> tuple[Path, ...]:
    paths = [ROOT / rel for rel in PRODUCTION_ENTRYPOINTS]
    for rel in PRODUCTION_DIRS:
        paths.extend((ROOT / rel).rglob("*.py"))
    return tuple(
        sorted(
            (path for path in paths if path.is_file()),
            key=lambda path: path.as_posix(),
        )
    )


def _call_target_name(node: ast.Call) -> str:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return ""


def _is_sys_modules(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "sys"
        and node.attr == "modules"
    )


def _target_writes_sys_modules(target: ast.AST) -> bool:
    return any(
        isinstance(node, ast.Subscript) and _is_sys_modules(node.value)
        for node in ast.walk(target)
    )


def test_critical_runtime_surfaces_have_no_import_or_assignment_surgery():
    for rel in CRITICAL_SURFACES:
        src = _source(rel)
        for token in FORBIDDEN_TEXT:
            assert token not in src, f"{rel} reintroduced runtime surgery: {token}"


def test_critical_runtime_surfaces_do_not_setattr_telegram_delivery_methods():
    for rel in CRITICAL_SURFACES:
        tree = ast.parse(_source(rel), filename=rel)
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "setattr"
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
            ):
                continue
            method = node.args[1].value
            assert method not in TELEGRAM_METHODS, (
                f"{rel} reintroduced Telegram delivery interception: {method}"
            )


def test_production_has_zero_install_calls_and_no_module_alias_surgery():
    install_calls: list[str] = []
    module_aliases: list[str] = []
    contextvar_calls: list[str] = []
    import_hook_findings: list[str] = []

    for path in _production_python_paths():
        rel = path.relative_to(ROOT).as_posix()
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src, filename=rel)

        if "sys.meta_path" in src or "MetaPathFinder" in src:
            import_hook_findings.append(rel)

        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                target_name = _call_target_name(node)
                if target_name.startswith("install_"):
                    install_calls.append(f"{rel}:{node.lineno}:{target_name}")
                if target_name == "ContextVar":
                    contextvar_calls.append(f"{rel}:{node.lineno}")
                if (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr in SYS_MODULE_MUTATORS
                    and _is_sys_modules(node.func.value)
                ):
                    module_aliases.append(
                        f"{rel}:{node.lineno}:sys.modules.{node.func.attr}"
                    )

            targets: tuple[ast.AST, ...] = ()
            if isinstance(node, ast.Assign):
                targets = tuple(node.targets)
            elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
                targets = (node.target,)
            if any(_target_writes_sys_modules(target) for target in targets):
                module_aliases.append(f"{rel}:{node.lineno}:sys.modules[...] assignment")

    findings = {
        "ROOT_CALLS": install_calls,
        "sys.modules": module_aliases,
        "ContextVar": contextvar_calls,
        "import_hooks": import_hook_findings,
    }
    nonempty = {key: value for key, value in findings.items() if value}
    assert nonempty == {}, (
        f"ROOT_CALLS={len(install_calls)}; zero-surgery findings={nonempty}"
    )


def test_runtime_manifest_critical_routes_are_contracts_not_patch_stacks():
    src = _source("services/runtime_manifest.py")
    assert '"livedub-qa-contract"' in src
    assert '"livedub-delivery-contract"' in src
    assert '"pre-main-quality-policy"' in src
    for legacy in (
        "livedub-long-qa",
        "livedub-qa-trust",
        "livedub-ru-provenance",
        "livedub-qa-hardening",
        "livedub-output-policy",
        "livedub-publication",
        "livedub-audio-companion",
        "livedub-audio-dedupe",
        "livedub-new-delivery-atomicity",
        "livedub-cached-delivery-atomicity",
        "livedub-deep-audit",
        "livedub-dual-audio-policy",
        "conspect-quality-bootstrap",
    ):
        assert f'"{legacy}"' not in src
