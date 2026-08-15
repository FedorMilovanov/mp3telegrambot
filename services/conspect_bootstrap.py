#!/usr/bin/env python3
"""Explicit pre-main composition for the existing conspect quality contract.

This moves the historical setup out of ``services.__init__`` so package imports
have no hidden side effects.  The domain-specific adapters remain isolated here
until their individual owners can absorb those contracts; startup ordering is
now declared by ``runtime_manifest`` rather than by Python import hooks.
"""
from __future__ import annotations


def configure_conspect_runtime() -> str:
    from core import content_audit as content_audit
    from core import prompts
    from core import structured_blocks
    from services import conspect_quality_contract as contract_module
    from services.conspect_audit_runtime import install_conspect_audit_runtime
    from services.conspect_quality_contract import install_conspect_quality_contract
    from services.study_synthesis_runtime import install_teacherly_study_runtime

    contract = install_conspect_quality_contract()
    audit = install_conspect_audit_runtime()

    legacy_effective_prompt = prompts.STUDY_ANALYSIS_PROMPT
    legacy_word_study_normalizer = contract_module.normalize_word_study_block

    synthesis = install_teacherly_study_runtime()

    # Preserve historical source-level contracts while the live Telegraph path
    # owns the teacherly effective prompt.  This is explicit bootstrap state,
    # not an import-time side effect.
    prompts.STUDY_ANALYSIS_PROMPT = legacy_effective_prompt
    contract_module.normalize_word_study_block = legacy_word_study_normalizer

    teacherly_normalizer = structured_blocks.normalize_structured_block

    def normalize_teacherly_with_drop(raw):
        normalized = teacherly_normalizer(raw)
        if normalized is None and isinstance(raw, dict):
            block_type = str(raw.get("type") or "").strip().lower()
            if block_type in {"word_study", "wordstudy"}:
                return {
                    "type": "paragraph",
                    "text": "__DROP_INCOMPLETE_WORD_STUDY__",
                    "_drop_word_study": True,
                }
        return normalized

    normalize_teacherly_with_drop._teacherly_study_runtime = True  # type: ignore[attr-defined]
    structured_blocks.normalize_structured_block = normalize_teacherly_with_drop
    content_audit.normalize_structured_block = normalize_teacherly_with_drop

    return f"{contract}; {audit}; {synthesis}"
