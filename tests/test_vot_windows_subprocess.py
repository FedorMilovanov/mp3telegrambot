from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from services import yandex_live_dub


def test_windows_batch_command_contains_only_fixed_placeholders() -> None:
    original = {
        "COMSPEC": r"C:\Windows\System32\cmd.exe",
        "MP3BOT_VOT_ARG_2": "stale",
    }
    url = "https://example.invalid/watch?a=1&b=2"
    arguments = ["vot-cli-live.cmd", "--url", url, "100%PATH%!delayed!", "C:\\folder\\"]

    command, child_env = yandex_live_dub._windows_batch_invocation(arguments, original)

    assert command == [
        r"C:\Windows\System32\cmd.exe",
        "/D",
        "/S",
        "/V:OFF",
        "/C",
        " ".join(f'"%MP3BOT_VOT_ARG_{index}%"' for index in range(len(arguments))),
    ]
    assert url not in command[-1]
    assert child_env["MP3BOT_VOT_ARG_2"] == url
    assert child_env["MP3BOT_VOT_ARG_3"] == "100%PATH%!delayed!"
    assert child_env["MP3BOT_VOT_ARG_4"] == arguments[-1] + "\\"
    assert original["MP3BOT_VOT_ARG_2"] == "stale"


@pytest.mark.parametrize("bad", ['"', "\r", "\n", "\0"])
def test_windows_batch_rejects_command_line_control_characters(bad: str) -> None:
    with pytest.raises(ValueError, match="arguments cannot contain"):
        yandex_live_dub._windows_batch_invocation(["vot-cli-live.cmd", bad], {})


def test_windows_wrapper_uses_placeholders_and_never_shell(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    monkeypatch.setattr(yandex_live_dub.sys, "platform", "win32")
    monkeypatch.setattr(yandex_live_dub.subprocess, "run", fake_run)
    url = "https://example.invalid/watch?a=1&b=2"

    assert yandex_live_dub._run_subprocess(["vot-cli-live.cmd", url], cwd=tmp_path, timeout=7) == ("ok", "", 0)
    command = captured["command"]
    kwargs = captured["kwargs"]
    assert isinstance(command, list)
    assert isinstance(kwargs, dict)
    assert url not in command[-1]
    assert kwargs["shell"] is False
    assert kwargs["env"]["MP3BOT_VOT_ARG_1"] == url
    assert kwargs["cwd"] == tmp_path
    assert kwargs["timeout"] == 7


@pytest.mark.skipif(sys.platform != "win32", reason="real cmd.exe roundtrip runs on Windows CI")
def test_windows_batch_arguments_roundtrip_through_real_cmd(tmp_path: Path) -> None:
    script = tmp_path / "echo arguments.cmd"
    script.write_text(
        f'@echo off\r\n"{sys.executable}" -X utf8 -c "import json,sys; print(json.dumps(sys.argv[1:]))" %*\r\n',
        encoding="utf-8",
    )
    arguments = [
        "plain",
        "with spaces",
        "https://example.invalid/watch?a=1&b=2",
        "100%PATH%!delayed!",
        "caret^ pipe| less< greater> (paren) semi; equals=",
        "русский текст",
        "one trailing\\",
        "two trailing\\\\",
        "",
    ]

    stdout, stderr, returncode = yandex_live_dub._run_subprocess([str(script), *arguments], timeout=30)

    assert returncode == 0, stderr or stdout
    assert json.loads(stdout) == arguments
