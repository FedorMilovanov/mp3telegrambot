#!/usr/bin/env python3
"""Temporary branch-only refactor runner for the zero-runtime-surgery marathon."""
from __future__ import annotations

import argparse
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    (ROOT / path).write_text(text, encoding="utf-8")


def remove_runtime_feature(text: str, feature_id: str) -> str:
    quoted = r"[\"']" + re.escape(feature_id) + r"[\"']"
    pattern = re.compile(r"\n    RuntimeFeature\(\n        " + quoted + r",.*?\n    \),", re.DOTALL)
    text2, count = pattern.subn("", text, count=1)
    if count != 1:
        raise RuntimeError(f"runtime manifest feature not found exactly once: {feature_id}")
    print(f"removed runtime manifest feature: {feature_id}")
    return text2


def wave3() -> None:
    runtime_path = "services/dub_studio_runtime.py"
    runtime = read(runtime_path)
    runtime = runtime.replace(
        '"""Install Dub Studio handlers, notifier and detached local worker.\n\nProgress delivery is intentionally part of this runtime rather than a separate\npatch layer: one Telegram status card is created per job and edited through all\nmilestones. Terminal success/failure updates that card and may send one detailed\nresult message.\n"""',
        '"""Source-owned Dub Studio composition, notifier and detached local worker.\n\nThe production Application explicitly registers handlers and starts notification\nservices. No PTB class methods are replaced at runtime.\n"""',
        1,
    )
    runtime = runtime.replace("import threading\n", "")
    runtime = runtime.replace("_INSTALLED = False\n_LOCK = threading.Lock()\n_ORIGINAL_BUILD = None\n_ORIGINAL_START = None\n", "")

    old = '''def install_dub_studio_runtime() -> None:\n    global _INSTALLED, _ORIGINAL_BUILD, _ORIGINAL_START\n    if _INSTALLED or not enabled():\n        return\n    with _LOCK:\n        if _INSTALLED:\n            return\n        from telegram.ext import Application, ApplicationBuilder\n        from handlers.dub_audio_repair import register_dub_audio_repair_handlers\n        from handlers.dub_commands import register_dub_handlers\n        from handlers.dub_delivery import register_dub_delivery_handlers\n        from handlers.dub_health import register_dub_health_handler\n        from handlers.dub_quickstart import register_dub_quickstart_handler\n        from handlers.dub_wizard import register_dub_wizard_handlers\n\n        _ORIGINAL_BUILD = ApplicationBuilder.build\n        _ORIGINAL_START = Application.start\n\n        def build_with_dub(self: Any) -> Any:\n            application = _ORIGINAL_BUILD(self)\n            register_dub_wizard_handlers(application)\n            register_dub_health_handler(application)\n            register_dub_handlers(application)\n            register_dub_audio_repair_handlers(application)\n            register_dub_delivery_handlers(application)\n            register_dub_quickstart_handler(application)\n            return application\n\n        async def start_with_dub(self: Any) -> None:\n            await _ORIGINAL_START(self)\n            if not self.bot_data.get("dub_studio_notification_task"):\n                task = self.create_task(\n                    _notification_loop(self),\n                    name="dub-studio-notifications",\n                )\n                self.bot_data["dub_studio_notification_task"] = task\n\n        ApplicationBuilder.build = build_with_dub\n        Application.start = start_with_dub\n        ensure_worker_running()\n        _INSTALLED = True\n        logger.info(\n            "🎙 Dub Studio runtime v4.5: direct max-quality + editable progress enabled"\n        )\n\n\n__all__ = ["enabled", "ensure_worker_running", "install_dub_studio_runtime"]'''
    new = '''def register_dub_studio(application: Any) -> bool:\n    """Register Dub Studio handlers on this concrete Application instance."""\n    if not enabled():\n        return False\n    from handlers.dub_audio_repair import register_dub_audio_repair_handlers\n    from handlers.dub_commands import register_dub_handlers\n    from handlers.dub_delivery import register_dub_delivery_handlers\n    from handlers.dub_health import register_dub_health_handler\n    from handlers.dub_quickstart import register_dub_quickstart_handler\n    from handlers.dub_wizard import register_dub_wizard_handlers\n\n    register_dub_wizard_handlers(application)\n    register_dub_health_handler(application)\n    register_dub_handlers(application)\n    register_dub_audio_repair_handlers(application)\n    register_dub_delivery_handlers(application)\n    register_dub_quickstart_handler(application)\n    ensure_worker_running()\n    logger.info("🎙 Dub Studio v4.5 handlers registered on Application")\n    return True\n\n\ndef start_dub_studio_services(application: Any) -> bool:\n    """Start the request-independent notifier after Application.start()."""\n    if not enabled():\n        return False\n    if application.bot_data.get("dub_studio_notification_task"):\n        return True\n    task = application.create_task(\n        _notification_loop(application),\n        name="dub-studio-notifications",\n    )\n    application.bot_data["dub_studio_notification_task"] = task\n    logger.info("🎙 Dub Studio notification service started")\n    return True\n\n\n__all__ = [\n    "enabled",\n    "ensure_worker_running",\n    "register_dub_studio",\n    "start_dub_studio_services",\n]'''
    if old not in runtime:
        raise RuntimeError("Dub Studio installer block anchor missing")
    runtime = runtime.replace(old, new, 1)
    write(runtime_path, runtime)

    main_path = "main.py"
    main = read(main_path)
    build_anchor = "    app = builder.build()\n\n"
    build_new = '''    app = builder.build()\n\n    from services.dub_studio_runtime import register_dub_studio, start_dub_studio_services\n    register_dub_studio(app)\n\n'''
    if build_anchor not in main:
        raise RuntimeError("main Application build anchor missing")
    main = main.replace(build_anchor, build_new, 1)

    start_anchor = '''        await app.initialize()\n        await app.start()\n        logger.info("📡 Запускаю polling getUpdates...")\n'''
    start_new = '''        await app.initialize()\n        await app.start()\n        start_dub_studio_services(app)\n        logger.info("📡 Запускаю polling getUpdates...")\n'''
    if start_anchor not in main:
        raise RuntimeError("main Application start anchor missing")
    main = main.replace(start_anchor, start_new, 1)
    write(main_path, main)

    manifest = read("services/runtime_manifest.py")
    manifest = remove_runtime_feature(manifest, "dub-studio-runtime")
    write("services/runtime_manifest.py", manifest)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("wave", choices=("wave3",))
    args = parser.parse_args()
    wave3()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
