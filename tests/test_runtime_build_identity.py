from __future__ import annotations

from services.runtime_build_identity import (
    RUNTIME_BUILD_IDENTITY_POLICY,
    runtime_build_identity_html_lines,
    runtime_build_identity_log_line,
    runtime_build_identity_payload,
)


def _payload():
    return runtime_build_identity_payload(
        build_sha="a" * 40,
        dirty="no",
        python_version="3.13.7",
        versions={
            "google-genai": "2.44.0",
            "faster-whisper": "1.2.0",
            "ctranslate2": "4.6.2",
        },
        factory_model="gemini-3.8-flash",
        gemini_client_count=4,
    )


def test_runtime_build_identity_is_strict_allowlist() -> None:
    payload = _payload()

    assert payload == {
        "policy": RUNTIME_BUILD_IDENTITY_POLICY,
        "build_sha": "a" * 40,
        "dirty": "no",
        "python": "3.13.7",
        "packages": {
            "google-genai": "2.44.0",
            "faster-whisper": "1.2.0",
            "ctranslate2": "4.6.2",
        },
        "factory_model": "gemini-3.8-flash",
        "gemini_client_count": 4,
    }
    assert "api_key" not in str(payload).casefold()
    assert "token" not in str(payload).casefold()
    assert "c:\\" not in str(payload).casefold()


def test_runtime_build_identity_formats_exact_versions_without_secrets() -> None:
    payload = _payload()
    log_line = runtime_build_identity_log_line(payload)
    html_lines = runtime_build_identity_html_lines(payload)
    rendered = "\n".join((log_line, *html_lines))

    assert "sha=aaaaaaaaaaaa" in log_line
    assert "dirty=no" in log_line
    assert "python=3.13.7" in log_line
    assert "google-genai=2.44.0" in log_line
    assert "faster-whisper=1.2.0" in log_line
    assert "ctranslate2=4.6.2" in log_line
    assert "factory_model=gemini-3.8-flash" in log_line
    assert "gemini_clients=4" in log_line
    assert "aaaaaaaaaaaa" in rendered
    assert "gemini-3.8-flash" in rendered


def test_invalid_build_sha_and_dirty_state_fail_safe() -> None:
    payload = runtime_build_identity_payload(
        build_sha="not-a-sha",
        dirty="C:/secret/worktree",
        python_version="3.13",
        versions={name: "x" for name in ("google-genai", "faster-whisper", "ctranslate2")},
        factory_model="gemini-3.8-flash",
        gemini_client_count=999,
    )

    assert payload["build_sha"] == "unknown"
    assert payload["dirty"] == "unknown"
    assert payload["gemini_client_count"] == 100
    assert "secret" not in runtime_build_identity_log_line(payload).casefold()
