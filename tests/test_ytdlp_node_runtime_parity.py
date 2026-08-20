from __future__ import annotations

from pathlib import Path

import services.ffmpeg as ffmpeg


def test_supported_js_runtimes_ignores_deno_when_node_is_valid(monkeypatch) -> None:
    def _which(name: str):
        return {
            "node": r"C:\\Program Files\\nodejs\\node.exe",
            "deno": r"C:\\Tools\\deno.exe",
        }.get(name)

    monkeypatch.setattr(ffmpeg.shutil, "which", _which)
    monkeypatch.setattr(
        ffmpeg,
        "_probe_js_runtime_version",
        lambda exe, _args: (22, 23, 1) if "node" in exe.lower() else (2, 6, 0),
    )

    assert ffmpeg._supported_js_runtimes() == ["node"]


def test_ytdlp_base_args_clear_default_deno_before_enabling_node(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(ffmpeg, "COOKIES_FILE", tmp_path / "missing-cookies.txt")
    monkeypatch.setattr(ffmpeg, "_firefox_cookie_source_available", lambda _spec="firefox": False)
    monkeypatch.setattr(ffmpeg, "_proxy_for_ytdlp", lambda: "")
    monkeypatch.setattr(ffmpeg, "_supported_js_runtimes", lambda: ["node"])
    monkeypatch.setenv("YTDLP_FRAGMENTS", "1")

    args = ffmpeg._build_ytdlp_base_args()

    clear_index = args.index("--no-js-runtimes")
    runtime_indexes = [i for i, value in enumerate(args) if value == "--js-runtimes"]
    assert len(runtime_indexes) == 1
    assert clear_index < runtime_indexes[0]
    assert args[runtime_indexes[0] + 1] == "node"
    assert "deno" not in args


def test_invalid_node_does_not_fall_back_to_unvalidated_deno(monkeypatch) -> None:
    def _which(name: str):
        return {
            "node": r"C:\\Program Files\\nodejs\\node.exe",
            "deno": r"C:\\Tools\\deno.exe",
        }.get(name)

    monkeypatch.setattr(ffmpeg.shutil, "which", _which)
    monkeypatch.setattr(ffmpeg, "_probe_js_runtime_version", lambda _exe, _args: (21, 9, 0))

    assert ffmpeg._supported_js_runtimes() == []
