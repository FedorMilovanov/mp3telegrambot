"""Runtime guard for the channel-specific Shorts caption punctuation contract.

The shared title-case layer is intentionally project-wide and may normalise
spaced dash variants. Shorts captions use an asymmetric public format:

- an internal semantic pause inside the headline is `` — ``;
- the external headline/author boundary is `` - ``.

This guard protects only the title-case callable copied into
``services.shorts_video``. Other titles and captions keep their established
project-wide punctuation policy.
"""
from __future__ import annotations

import re
from typing import Callable

_INSTALLED = False
_SENTINEL = "\ue000SHORTS_EM_DASH\ue001"
_SPACED_DASH_RE = re.compile(r"(?<=\S)\s+(?:-|–|—)\s+(?=\S)")


def _protect_internal_em_dash(
    title_case: Callable[[str], str],
) -> Callable[[str], str]:
    """Wrap one title-case function while preserving spaced semantic dashes."""

    def wrapped(value: str) -> str:
        text = str(value or "")
        if not text:
            return text
        protected = _SPACED_DASH_RE.sub(f" {_SENTINEL} ", text)
        titled = title_case(protected)
        return str(titled or "").replace(_SENTINEL, "—")

    wrapped._shorts_caption_contract = True  # type: ignore[attr-defined]
    wrapped.__name__ = getattr(title_case, "__name__", "title_case_fragment")
    wrapped.__doc__ = getattr(title_case, "__doc__", None)
    return wrapped


def install_short_caption_contract_runtime() -> str:
    """Install the narrow guard idempotently and return its policy marker."""
    global _INSTALLED
    from services import shorts_video

    current = shorts_video.title_case_fragment
    if not getattr(current, "_shorts_caption_contract", False):
        shorts_video.title_case_fragment = _protect_internal_em_dash(current)
    _INSTALLED = True
    return "internal=em-dash; title-author=hyphen; word-hyphens=preserved"


__all__ = [
    "install_short_caption_contract_runtime",
]
