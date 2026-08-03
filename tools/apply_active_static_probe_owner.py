#!/usr/bin/env python3
"""Move the installed Shorts static detector to the shared process owner."""
from __future__ import annotations

import ast
from pathlib import Path


RUNTIME = Path("services/shorts_static_runtime.py")
RUNTIME_TEST = Path("tests/test_shorts_static_runtime.py")
PROBE_TEST = Path("tests/test_ffmpeg_probe_ownership.py")


def _replace_once(text: str, old: str, new: str, *, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


def main() -> None:
    runtime = RUNTIME.read_text(encoding="utf-8")
    runtime = _replace_once(runtime, "import asyncio\n", "", label="asyncio import")
    runtime = _replace_once(runtime, "import subprocess\n", "", label="subprocess import")
    runtime = _replace_once(
        runtime,
        "from typing import Iterable\n",
        "from typing import Iterable\n\n"
        "from services.async_process import run_cancellable_process\n",
        label="owner import",
    )
    runtime = _replace_once(
        runtime,
        "    proc = await asyncio.get_running_loop().run_in_executor(\n"
        "        None,\n"
        "        lambda: subprocess.run(\n"
        "            cmd,\n"
        "            capture_output=True,\n"
        "            text=True,\n"
        "            encoding=\"utf-8\",\n"
        "            errors=\"replace\",\n"
        "            timeout=45,\n"
        "        ),\n"
        "    )",
        "    proc = await run_cancellable_process(\n"
        "        cmd, timeout=45, text=True\n"
        "    )",
        label="active motion probe",
    )
    ast.parse(runtime)
    if "run_in_executor" in runtime or "subprocess.run(" in runtime:
        raise SystemExit("active static runtime still contains executor subprocess")
    if runtime.count("await run_cancellable_process(") != 1:
        raise SystemExit("active static runtime owner count changed")

    runtime_test = RUNTIME_TEST.read_text(encoding="utf-8")
    runtime_test = _replace_once(
        runtime_test,
        "    def fake_run(cmd, **kwargs):\n"
        "        seen.append(list(cmd))\n"
        "        return SimpleNamespace(returncode=0, stdout=\"\", stderr=_static_probe_output())\n\n"
        "    monkeypatch.setattr(runtime.shutil, \"which\", lambda name: \"ffmpeg\")\n"
        "    monkeypatch.setattr(runtime.subprocess, \"run\", fake_run)\n",
        "    async def fake_owner(cmd, **kwargs):\n"
        "        seen.append(list(cmd))\n"
        "        assert kwargs == {\"timeout\": 45, \"text\": True}\n"
        "        return SimpleNamespace(returncode=0, stdout=\"\", stderr=_static_probe_output())\n\n"
        "    monkeypatch.setattr(runtime.shutil, \"which\", lambda name: \"ffmpeg\")\n"
        "    monkeypatch.setattr(runtime, \"run_cancellable_process\", fake_owner)\n",
        label="two static probes fake owner",
    )
    runtime_test = _replace_once(
        runtime_test,
        "    def failed_run(cmd, **kwargs):\n"
        "        return SimpleNamespace(returncode=1, stdout=\"\", stderr=\"decode failed\")\n\n"
        "    monkeypatch.setattr(runtime.subprocess, \"run\", failed_run)\n",
        "    async def failed_owner(cmd, **kwargs):\n"
        "        return SimpleNamespace(returncode=1, stdout=\"\", stderr=\"decode failed\")\n\n"
        "    monkeypatch.setattr(runtime, \"run_cancellable_process\", failed_owner)\n",
        label="failed active probe owner",
    )
    runtime_test = _replace_once(
        runtime_test,
        "    def fake_run(cmd, **kwargs):\n"
        "        return SimpleNamespace(returncode=0, stdout=\"\", stderr=next(outputs))\n\n"
        "    monkeypatch.setattr(runtime.shutil, \"which\", lambda name: \"ffmpeg\")\n"
        "    monkeypatch.setattr(runtime.subprocess, \"run\", fake_run)\n",
        "    async def fake_owner(cmd, **kwargs):\n"
        "        return SimpleNamespace(returncode=0, stdout=\"\", stderr=next(outputs))\n\n"
        "    monkeypatch.setattr(runtime.shutil, \"which\", lambda name: \"ffmpeg\")\n"
        "    monkeypatch.setattr(runtime, \"run_cancellable_process\", fake_owner)\n",
        label="opening slide active owner",
    )
    runtime_test = _replace_once(
        runtime_test,
        "    assert \"SHORTS_STATIC_SECOND_PROBE_OFFSET\" in runtime_source\n",
        "    assert \"SHORTS_STATIC_SECOND_PROBE_OFFSET\" in runtime_source\n"
        "    assert \"await run_cancellable_process(\" in runtime_source\n"
        "    assert \"run_in_executor\" not in runtime_source\n"
        "    assert \"subprocess.run(\" not in runtime_source\n",
        label="runtime source contract",
    )
    ast.parse(runtime_test)

    probe_test = PROBE_TEST.read_text(encoding="utf-8")
    obsolete = (
        "\n\n@pytest.mark.asyncio\n"
        "async def test_freeze_probe_preserves_static_decision(monkeypatch, tmp_path) -> None:\n"
        "    video_path = tmp_path / \"video.mp4\"\n"
        "    video_path.write_bytes(b\"video\")\n\n"
        "    async def fake_owner(command, **kwargs):\n"
        "        return subprocess.CompletedProcess(\n"
        "            command,\n"
        "            0,\n"
        "            \"\",\n"
        "            \"freeze_start: 0\\nfreeze_end: 5\\nfreeze_duration: 5.0\\n\",\n"
        "        )\n\n"
        "    monkeypatch.setattr(ffmpeg.shutil, \"which\", lambda name: \"ffmpeg\")\n"
        "    monkeypatch.setattr(ffmpeg, \"run_cancellable_process\", fake_owner)\n\n"
        "    assert await ffmpeg._is_static_video(video_path, probe_seconds=6.0) is True\n"
    )
    probe_test = _replace_once(
        probe_test,
        obsolete,
        "",
        label="obsolete patched-symbol freeze test",
    )
    probe_test = _replace_once(
        probe_test,
        "SOURCE_PATH = Path(\"services/ffmpeg.py\")\n",
        "SOURCE_PATH = Path(\"services/ffmpeg.py\")\n"
        "STATIC_RUNTIME_PATH = Path(\"services/shorts_static_runtime.py\")\n",
        label="runtime path",
    )
    probe_test = _replace_once(
        probe_test,
        "    assert \"from subprocess import run\" not in selected\n",
        "    assert \"from subprocess import run\" not in selected\n\n"
        "    runtime_source = STATIC_RUNTIME_PATH.read_text(encoding=\"utf-8\")\n"
        "    assert runtime_source.count(\"await run_cancellable_process(\") == 1\n"
        "    assert \"run_in_executor\" not in runtime_source\n"
        "    assert \"subprocess.run(\" not in runtime_source\n",
        label="active runtime source contract",
    )
    ast.parse(probe_test)

    RUNTIME.write_text(runtime, encoding="utf-8")
    RUNTIME_TEST.write_text(runtime_test, encoding="utf-8")
    PROBE_TEST.write_text(probe_test, encoding="utf-8")
    print("patched installed static detector and active runtime tests")


if __name__ == "__main__":
    main()
