#!/usr/bin/env python3
"""Fail-closed boundary around the optional Highlights quality pipeline."""
from __future__ import annotations

import asyncio
import logging
import subprocess
from pathlib import Path
from typing import Any

from services.highlights_quality import refine_highlights_candidate

logger = logging.getLogger(__name__)

_PROCESS_NOT_REAPED_MESSAGE = "child process did not stop after terminate/kill"


def _failure_report(reason: str, *, detail: str = "") -> dict[str, Any]:
    report: dict[str, Any] = {
        "policy": "highlights-quality-boundary-v1",
        "accepted": False,
        "reason": reason,
        "rejections": [],
    }
    if detail:
        report["detail"] = detail[:240]
    return report


async def verify_highlights_candidate(
    source_video_path: Path,
    candidate: dict,
    *,
    ai_data: dict | None = None,
    source_duration: float = 0.0,
) -> tuple[dict | None, dict[str, Any]]:
    """Return structured evidence for every quality-gate failure.

    The internal verifier already classifies normal rejections. This boundary
    owns infrastructure failures from probe rendering and child-process
    management so an optional Highlights reel cannot escape as an unstructured
    pipeline exception. Cancellation is never converted into a rejection.
    """
    try:
        return await refine_highlights_candidate(
            source_video_path,
            candidate,
            ai_data=ai_data,
            source_duration=source_duration,
        )
    except asyncio.CancelledError:
        raise
    except subprocess.TimeoutExpired as exc:
        logger.warning(
            "Highlights source-context probe timed out after %ss: %s",
            exc.timeout,
            exc.cmd,
        )
        return None, _failure_report(
            "probe_render_timeout",
            detail=f"timeout={exc.timeout}",
        )
    except RuntimeError as exc:
        detail = str(exc)
        if _PROCESS_NOT_REAPED_MESSAGE in detail:
            logger.error(
                "Highlights source-context probe ownership failed: %s",
                detail[:240],
            )
            return None, _failure_report(
                "probe_process_not_reaped",
                detail=detail,
            )
        logger.exception(
            "Highlights quality gate raised an unrelated RuntimeError"
        )
        return None, _failure_report(
            "quality_gate_error:RuntimeError",
            detail=detail,
        )
    except Exception as exc:
        logger.exception(
            "Highlights quality gate failed before a structured decision: %s",
            type(exc).__name__,
        )
        return None, _failure_report(
            f"quality_gate_error:{type(exc).__name__}",
            detail=str(exc),
        )


__all__ = ["verify_highlights_candidate"]
