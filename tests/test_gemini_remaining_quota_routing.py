from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def function_source(relpath: str, name: str) -> str:
    path = ROOT / relpath
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    matches = [
        node for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
    ]
    assert len(matches) == 1, (relpath, name, len(matches))
    return ast.get_source_segment(text, matches[0]) or ""


def test_simple_runtime_routes_use_common_project_aware_router() -> None:
    cases = (
        ("services/eng_subtitles.py", "_translate_chunk_with_retry"),
        ("services/livedub_info_presentation_policy.py", "_translate_title_second_chance"),
        ("services/livedub_publication.py", "_generate_quality_publication"),
        ("services/shorts_factory_publication.py", "_generate_descriptions"),
    )
    for relpath, name in cases:
        source = function_source(relpath, name)
        assert "gemini_generate(" in source
        assert "response_validator=" in source
        assert "client.aio.models.generate_content" in source
        assert "for client_index, client in enumerate(GEMINI_CLIENTS)" not in source
        assert "GEMINI_CLIENTS[attempt %" not in source


def test_bounded_publication_core_skips_project_siblings_before_attempt_claim() -> None:
    source = function_source("services/livedub_publication_core.py", "_generate_quality")
    assert "ProjectQuotaDomainTracker" in source
    assert "record_project_scoped_error" in source
    assert source.index("quota_tracker.should_skip") < source.index("used += 1")


def test_legacy_factory_keeps_files_and_inference_quota_scopes_separate() -> None:
    source = function_source("services/shorts_factory_candidates.py", "create_factory_plan")
    assert "ProjectQuotaDomainTracker" in source
    assert 'files_scope = "shorts_factory_legacy_files"' in source
    assert 'inference_scope = "shorts_factory_legacy_inference"' in source
    assert "record_project_scoped_error(client, quota_scope, exc)" in source
    assert source.index("quota_tracker.should_skip(client, inference_scope)") < source.index("uploaded_name = """)


def test_sync_voxcpm_route_uses_slot_aligned_project_domain_metadata() -> None:
    source = function_source("tools/voxcpm2/generic_short_production.py", "gemini_json")
    assert "keys = _translation_keys()" in source
    assert "key_domain_entries(raw_slot_keys, deduplicate=True)" in source
    assert "domain_by_key" in source
    assert "is_project_scoped_quota_error(exc)" in source
    assert "exhausted_project_domains" in source
    assert "len(keys)" not in source
    assert "len(key_entries)" in source
    assert source.index("quota_domain in exhausted_project_domains") < source.index("_translation_client(api_key")


def test_audio_timestamp_repair_is_one_pinned_same_client_pass() -> None:
    source = function_source("services/gemini_analyze.py", "_repair_timestamp_coverage_if_needed")
    assert source.count("client.aio.models.generate_content(") == 1
    assert "contents=[audio_part, prompt]" in source
    assert "gemini_generate(" not in source
    assert "for client in" not in source


def test_audio_low_thinking_recovery_is_one_pinned_same_client_pass() -> None:
    source = function_source("services/gemini_analyze.py", "_retry_low_thinking")
    assert source.count("used_client.aio.models.generate_content(") == 1
    assert "contents=[used_audio_part, prompt]" in source
    assert "gemini_generate(" not in source
    assert "for client in" not in source


def test_census_covers_additional_quota_consuming_model_calls() -> None:
    source = (ROOT / "scripts" / "gemini_caller_census.py").read_text(encoding="utf-8")
    for suffix in (
        "aio.models.generate_content_stream",
        "models.generate_content_stream",
        "aio.models.count_tokens",
        "models.count_tokens",
        "aio.models.embed_content",
        "models.embed_content",
    ):
        assert f'"{suffix}"' in source
