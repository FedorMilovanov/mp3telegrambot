"""Regression coverage for long LiveDub tail preservation and diagnostics."""

from services.livedub_mix import build_mix_filter
from services.shorts_factory_execution_guard import _translation_source_error


def test_ducking_sidechain_control_survives_full_tail_without_padding_audible_ru():
    graph = build_mix_filter(
        0.45,
        1.3,
        600,
        duck=True,
        voice_eq=False,
        tail_pad_ms=1600,
    )

    # EN receives the real finite safety tail. The detector-only RU copy is
    # padded indefinitely so sidechaincompress follows EN to its EOF; the
    # audible [ru2] branch remains the real translated track and is not padded.
    assert "[en0]" in graph
    assert "apad=pad_dur=1.600" in graph
    assert "[ru1]apad[ru_sc];" in graph
    assert "[en0][ru_sc]sidechaincompress=" in graph
    assert "[enduck][ru2]amix=inputs=2:duration=longest" in graph
    assert "[ru2]apad" not in graph


def test_ducking_without_tail_keeps_original_sidechain_route():
    graph = build_mix_filter(
        0.45,
        1.3,
        600,
        duck=True,
        voice_eq=False,
        tail_pad_ms=0,
    )

    assert "[ru1]apad[ru_sc]" not in graph
    assert "[en0][ru1]sidechaincompress=" in graph


def test_local_factory_livedub_quality_failure_is_not_reported_as_provider_unavailable():
    message = _translation_source_error(
        RuntimeError(
            "Maximum-quality Factory LiveDub lost the required Russian tail: "
            "original=3234.381s required=3235.981s final=3235.023s"
        )
    )

    assert "получен" in message
    assert "локальная сборка" in message
    assert "не прошла обязательную проверку качества" in message
    assert "недоступен для этого источника" not in message


def test_real_provider_unavailable_marker_keeps_provider_error_message():
    message = _translation_source_error(
        RuntimeError("LIVEDUB_NOT_AVAILABLE: translation is not ready")
    )

    assert "недоступен для этого источника" in message
    assert "собственный нейроперевод" in message
