from __future__ import annotations

import logging

from services import livedub_publication_error_diagnostics as diagnostics


def _record(msg, args, *, name="services.livedub_publication"):
    return logging.LogRecord(
        name=name,
        level=logging.WARNING,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=args,
        exc_info=None,
    )


def test_empty_model_client_error_becomes_explicit_message():
    record = _record(
        "[LiveDubPublication] model=%s client=%d failed: %s",
        ("gemini-test", 2, ""),
    )

    assert diagnostics._PublicationExceptionFilter().filter(record) is True
    rendered = record.getMessage()

    assert "model=gemini-test client=2" in rendered
    assert "EmptyExceptionMessage" in rendered
    assert not rendered.rstrip().endswith("failed:")


def test_empty_title_fallback_error_becomes_explicit_message():
    record = _record("[LiveDubPublication] title fallback: %s", (None,))

    diagnostics._PublicationExceptionFilter().filter(record)

    assert record.getMessage().endswith(diagnostics._EMPTY_DETAIL)


def test_nonempty_detail_uses_project_credential_masker(monkeypatch):
    import core.utils

    monkeypatch.setattr(
        core.utils,
        "mask_api_key",
        lambda text: str(text).replace("top-secret", "***MASKED***"),
    )
    record = _record(
        "[LiveDubPublication] title fallback: %s",
        ("proxy top-secret failed",),
    )

    diagnostics._PublicationExceptionFilter().filter(record)
    rendered = record.getMessage()

    assert "top-secret" not in rendered
    assert "***MASKED***" in rendered


def test_unrelated_publication_log_is_untouched():
    record = _record("[LiveDubPublication] generated title=%s", ("Title",))
    original_args = record.args

    diagnostics._PublicationExceptionFilter().filter(record)

    assert record.args == original_args
    assert record.getMessage().endswith("Title")


def test_other_logger_is_untouched():
    record = _record(
        "[LiveDubPublication] title fallback: %s",
        ("",),
        name="services.some_other_module",
    )

    diagnostics._PublicationExceptionFilter().filter(record)

    assert record.getMessage().endswith("title fallback: ")


def test_installer_is_idempotent(monkeypatch):
    logger = logging.getLogger("services.livedub_publication")
    original_filters = list(logger.filters)
    try:
        logger.filters = [
            item for item in logger.filters
            if not isinstance(item, diagnostics._PublicationExceptionFilter)
        ]
        monkeypatch.setattr(diagnostics, "_INSTALLED", False)

        diagnostics.install_livedub_publication_error_diagnostics()
        diagnostics.install_livedub_publication_error_diagnostics()

        installed = [
            item for item in logger.filters
            if isinstance(item, diagnostics._PublicationExceptionFilter)
        ]
        assert len(installed) == 1
    finally:
        logger.filters = original_filters
