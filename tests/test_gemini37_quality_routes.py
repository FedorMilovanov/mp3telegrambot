#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import ModuleType

import pytest

from services import gemini37_quality_routes as routes


def test_quality_chain_is_37_then_36_and_rejects_35(monkeypatch):
    monkeypatch.setenv("GEMINI_MODEL", "gemini-3.7-flash")
    monkeypatch.setenv("GEMINI_QUALITY_FALLBACK_MODELS", "gemini-3.6-flash")
    assert routes.quality_model_chain() == (
        "gemini-3.7-flash",
        "gemini-3.6-flash",
    )

    monkeypatch.setenv("GEMINI_QUALITY_FALLBACK_MODELS", "gemini-3.5-flash")
    with pytest.raises(RuntimeError, match="Semantic fallback"):
        routes.quality_model_chain()


def test_full_sermon_review_uses_37_then_36_without_global_model_swap(monkeypatch):
    module = ModuleType("services.translation_editorial_factory")

    async def legacy_review(_pack_path):
        raise AssertionError("legacy 3.6-only review must be replaced")

    module.generate_gemini_editorial_review = legacy_review
    module.FACTORY_EDITORIAL_GEMINI_MODEL = "gemini-3.6-flash"
    monkeypatch.setitem(sys.modules, "services.translation_editorial_factory", module)
    monkeypatch.setenv("GEMINI_MODEL", "gemini-3.7-flash")
    monkeypatch.setenv("GEMINI_QUALITY_FALLBACK_MODELS", "gemini-3.6-flash")

    calls: list[str] = []

    async def fake_generate(editorial, pack_path: Path, *, model: str):
        assert editorial is module
        assert pack_path == Path("review.zip")
        calls.append(model)
        if model == "gemini-3.7-flash":
            return None
        return {"reviewer": f"gemini:{model}", "full_sermon": {}}

    monkeypatch.setattr(routes, "_generate_editorial_for_model", fake_generate)
    routes._install_editorial_model_policy()

    review = asyncio.run(module.generate_gemini_editorial_review(Path("review.zip")))

    assert calls == ["gemini-3.7-flash", "gemini-3.6-flash"]
    assert review["reviewer"] == "gemini:gemini-3.6-flash"
    assert module.FACTORY_EDITORIAL_GEMINI_MODEL == "gemini-3.7-flash"
    assert getattr(module.generate_gemini_editorial_review, "_mp3bot_gemini37_route", False)
