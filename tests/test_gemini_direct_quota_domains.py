from __future__ import annotations

from services.gemini_quota_domains import ProjectQuotaDomainTracker


class _ProjectQuotaError(RuntimeError):
    code = 429


class _GenericQuotaError(RuntimeError):
    code = 429


def test_project_scoped_quota_suppresses_only_same_domain_and_scope() -> None:
    first = object()
    same_project = object()
    other_project = object()
    tracker = ProjectQuotaDomainTracker(
        [first, same_project, other_project],
        ["project-a", "PROJECT-A", "project-b"],
    )

    exc = _ProjectQuotaError(
        "429 RESOURCE_EXHAUSTED GenerateRequestsPerDayPerProjectPerModel-FreeTier"
    )
    assert tracker.record_project_scoped_error(first, "inference", exc) is True
    assert tracker.should_skip(same_project, "inference") is True
    assert tracker.should_skip(other_project, "inference") is False
    assert tracker.should_skip(same_project, "files") is False


def test_generic_429_does_not_suppress_sibling_credential() -> None:
    first = object()
    sibling = object()
    tracker = ProjectQuotaDomainTracker(
        [first, sibling],
        ["project-a", "project-a"],
    )

    exc = _GenericQuotaError("429 RESOURCE_EXHAUSTED rate limit exceeded")
    assert tracker.record_project_scoped_error(first, "inference", exc) is False
    assert tracker.should_skip(sibling, "inference") is False


def test_unknown_or_unlabeled_clients_fail_open() -> None:
    configured = object()
    unlabeled = object()
    unknown = object()
    tracker = ProjectQuotaDomainTracker(
        [configured, unlabeled],
        ["project-a", ""],
    )
    exc = _ProjectQuotaError(
        "429 RESOURCE_EXHAUSTED requestsPerDayPerProject quota exhausted"
    )

    assert tracker.record_project_scoped_error(unknown, "files", exc) is False
    assert tracker.record_project_scoped_error(unlabeled, "files", exc) is False
    assert tracker.should_skip(unknown, "files") is False
    assert tracker.should_skip(unlabeled, "files") is False


def test_multiple_scopes_can_gate_an_expensive_followup_without_cross_contamination() -> None:
    first = object()
    sibling = object()
    tracker = ProjectQuotaDomainTracker(
        [first, sibling],
        ["project-a", "project-a"],
    )
    exc = _ProjectQuotaError(
        "429 RESOURCE_EXHAUSTED GenerateRequestsPerDayPerProjectPerModel-FreeTier"
    )

    assert tracker.record_project_scoped_error(first, "inference", exc) is True
    assert tracker.should_skip(sibling, "files") is False
    assert tracker.should_skip(sibling, "files", "inference") is True
