#!/usr/bin/env python3
"""Branch-exact AST census and fail-closed classification of Gemini routing.

The scanner intentionally imports no repository modules. CI therefore audits the
exact checkout, including optional-dependency and Windows-only paths.

Categories:
A - central project-aware router or a callback invoked through it.
B - explicit project-aware special transport / client factory.
C - intentionally pinned same-client recovery that preserves uploaded-file affinity.
D - tests/config-only usage.
E - unclassified runtime Gemini surface (CI failure).
"""

from __future__ import annotations

import argparse
import ast
from collections import Counter
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    "node_modules",
    ".pytest_cache",
    ".ruff_cache",
}

DIRECT_SUFFIXES = {
    "aio.models.generate_content": "inference_async",
    "models.generate_content": "inference",
    "aio.models.generate_content_stream": "inference_stream_async",
    "models.generate_content_stream": "inference_stream",
    "aio.models.count_tokens": "token_count_async",
    "models.count_tokens": "token_count",
    "aio.models.embed_content": "embedding_async",
    "models.embed_content": "embedding",
    "aio.files.upload": "files_upload_async",
    "files.upload": "files_upload",
    "aio.files.get": "files_get_async",
    "files.get": "files_get",
}

# Runtime direct-SDK surfaces are allowed only when the architectural owner is
# explicit. The integer caps the number of AST call sites for the signature, so
# adding another raw SDK call inside an already-approved function still fails CI.
# Keys are (path, function, kind) and values are (category, max_call_sites).
DIRECT_CLASSIFICATIONS: dict[tuple[str, str, str], tuple[str, int]] = {
    ("core/globals.py", "_make_gemini_client", "client_create"): ("B", 2),
    ("pipelines/main_pipeline.py", "_generate_title", "inference_async"): ("A", 1),
    ("services/eng_subtitles.py", "_generate_chunk", "inference_async"): ("A", 1),
    ("services/gemini_analyze.py", "_repair_timestamp_coverage_if_needed", "inference_async"): ("C", 1),
    ("services/gemini_analyze.py", "upload_to_client", "files_upload_async"): ("B", 1),
    ("services/gemini_analyze.py", "upload_to_client", "files_get_async"): ("B", 1),
    ("services/gemini_analyze.py", "_generate_once", "inference_async"): ("B", 2),
    ("services/gemini_analyze.py", "_retry_low_thinking", "inference_async"): ("C", 1),
    ("services/highlights_quality.py", "_call", "inference_async"): ("A", 1),
    ("services/livedub_info.py", "_call", "inference_async"): ("A", 1),
    ("services/livedub_info_presentation_policy.py", "_generate_title", "inference_async"): ("A", 1),
    ("services/livedub_publication.py", "_generate_publication", "inference_async"): ("A", 1),
    ("services/livedub_publication_core.py", "_generate_quality", "inference_async"): ("B", 1),
    ("services/livedub_qa.py", "_upload_and_wait", "files_upload_async"): ("B", 1),
    ("services/livedub_qa.py", "_upload_and_wait", "files_get_async"): ("B", 1),
    ("services/livedub_qa.py", "_attempt", "inference_async"): ("B", 2),
    ("services/quiz_generator.py", "_generate_one", "inference_async"): ("B", 1),
    ("services/render_clips_montage.py", "_generate_extras_content", "inference_async"): ("A", 2),
    ("services/shorts_candidates.py", "_generate_audio_candidate_content", "inference_async"): ("B", 2),
    ("services/shorts_candidates.py", "_upload", "files_upload_async"): ("B", 2),
    ("services/shorts_candidates.py", "_upload", "files_get_async"): ("B", 2),
    ("services/shorts_factory_candidates.py", "_wait_uploaded_file", "files_get_async"): ("B", 1),
    ("services/shorts_factory_candidates.py", "_run_pass", "inference_async"): ("B", 1),
    ("services/shorts_factory_candidates.py", "create_factory_plan", "files_upload_async"): ("B", 1),
    ("services/shorts_factory_capacity.py", "factory_gemini_clients", "client_create"): ("B", 1),
    ("services/shorts_factory_capacity_runtime.py", "_wait_factory_upload", "files_get_async"): ("B", 1),
    ("services/shorts_factory_capacity_runtime.py", "create_factory_plan_resumable", "files_upload_async"): ("B", 1),
    ("services/shorts_factory_publication.py", "_generate_descriptions_once", "inference_async"): ("A", 1),
    ("services/telegraph.py", "_generate_synopsis_content", "inference_async"): ("B", 2),
    ("services/telegraph.py", "_upload", "files_upload_async"): ("B", 1),
    ("services/telegraph.py", "_upload", "files_get_async"): ("B", 1),
    ("services/telegraph.py", "_upload_retry", "files_upload_async"): ("B", 1),
    ("services/telegraph.py", "_upload_retry", "files_get_async"): ("B", 1),
    ("services/telegraph_pages.py", "_request_on_client", "inference_async"): ("A", 2),
    ("services/translation_editorial_factory.py", "generate_gemini_editorial_review", "inference_async"): ("B", 1),
    ("tools/voxcpm2/generic_short_production.py", "gemini_json", "inference"): ("B", 1),
    ("tools/voxcpm2/generic_short_production.py", "_translation_client", "client_create"): ("B", 1),
}

CATEGORY_LABELS = {
    "A": "router",
    "B": "special",
    "C": "pinned",
    "D": "test/config",
    "E": "UNCLASSIFIED",
}


@dataclass(frozen=True, order=True)
class Finding:
    path: str
    line: int
    function: str
    kind: str
    expression: str

    @property
    def signature(self) -> tuple[str, str, str]:
        return (self.path, self.function, self.kind)


def _dotted(node: ast.AST) -> str:
    try:
        return ast.unparse(node)
    except Exception:
        return ""


def _function_name(parents: list[ast.AST]) -> str:
    for node in reversed(parents):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return node.name
    return "<module>"


class _Visitor(ast.NodeVisitor):
    def __init__(self, relpath: str) -> None:
        self.relpath = relpath
        self.parents: list[ast.AST] = []
        self.findings: list[Finding] = []

    def visit(self, node: ast.AST) -> None:  # type: ignore[override]
        self.parents.append(node)
        try:
            super().visit(node)
        finally:
            self.parents.pop()

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        expr = _dotted(node.func)
        function = _function_name(self.parents[:-1])

        direct_kind = None
        for suffix, kind in DIRECT_SUFFIXES.items():
            if expr == suffix or expr.endswith(f".{suffix}"):
                direct_kind = kind
                break
        if direct_kind:
            self.findings.append(
                Finding(self.relpath, node.lineno, function, direct_kind, expr)
            )

        if expr == "gemini_generate" or expr.endswith(".gemini_generate"):
            self.findings.append(
                Finding(self.relpath, node.lineno, function, "common_router", expr)
            )

        if expr in {"genai.Client", "google.genai.Client"} or expr.endswith(".genai.Client"):
            self.findings.append(
                Finding(self.relpath, node.lineno, function, "client_create", expr)
            )

        self.generic_visit(node)


def _python_files() -> list[Path]:
    result: list[Path] = []
    for path in ROOT.rglob("*.py"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        result.append(path)
    return sorted(result)


def collect() -> list[Finding]:
    findings: list[Finding] = []
    for path in _python_files():
        rel = path.relative_to(ROOT).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        except (OSError, UnicodeError, SyntaxError) as exc:
            raise SystemExit(f"cannot parse {rel}: {exc}") from exc
        visitor = _Visitor(rel)
        visitor.visit(tree)
        findings.extend(visitor.findings)
    return sorted(findings)


def classify(item: Finding) -> str:
    if item.path.startswith("tests/"):
        return "D"
    if item.kind == "common_router":
        return "A"
    approved = DIRECT_CLASSIFICATIONS.get(item.signature)
    return approved[0] if approved else "E"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--report-only",
        action="store_true",
        help="print classifications but do not fail on unknown/count drift",
    )
    args = parser.parse_args()

    findings = collect()
    categories = Counter()
    unknown: list[Finding] = []
    direct_counts: Counter[tuple[str, str, str]] = Counter()

    print(f"Gemini caller census: {len(findings)} findings")
    for item in findings:
        category = classify(item)
        categories[category] += 1
        if item.kind != "common_router" and not item.path.startswith("tests/"):
            direct_counts[item.signature] += 1
        if category == "E":
            unknown.append(item)
        print(
            f"{category}:{CATEGORY_LABELS[category]}\t{item.kind}\t"
            f"{item.path}:{item.line}\t{item.function}\t{item.expression}"
        )

    count_drift: list[tuple[tuple[str, str, str], int, int]] = []
    for signature, actual in sorted(direct_counts.items()):
        approved = DIRECT_CLASSIFICATIONS.get(signature)
        if approved is None:
            continue
        _category, maximum = approved
        if actual > maximum:
            count_drift.append((signature, actual, maximum))

    print(
        "Gemini caller classification summary: "
        + " ".join(f"{key}={categories.get(key, 0)}" for key in ("A", "B", "C", "D", "E"))
    )

    if unknown:
        print("Unclassified runtime Gemini callers:")
        for item in unknown:
            print(
                f"  {item.path}:{item.line} {item.function} "
                f"{item.kind} {item.expression}"
            )

    if count_drift:
        print("Approved direct-call signature count drift:")
        for signature, actual, maximum in count_drift:
            print(f"  {signature}: actual={actual} approved_max={maximum}")

    if args.report_only:
        return 0
    if unknown or count_drift:
        raise SystemExit(
            "Gemini caller census failed closed: classify the new runtime route "
            "or remove the extra direct SDK call."
        )
    print("Gemini caller census strict gate: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
