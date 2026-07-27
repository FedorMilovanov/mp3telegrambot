from __future__ import annotations

from pathlib import Path

import pytest

from core.dub_projects import (
    attach_approved_translation,
    create_project,
    load_project,
    save_project,
)
from pipelines.dubbing import job_queue


@pytest.fixture()
def ready_projects(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DUB_PROJECTS_DIR", str(tmp_path / "projects"))
    monkeypatch.setenv("DUB_QUEUE_DB", str(tmp_path / "queue.sqlite3"))

    def create(*, owner: int, url: str, profile: str = "shorts_premium") -> str:
        project_id = create_project(
            owner_user_id=owner,
            source={"kind": "url", "url": url},
        )["project_id"]
        manifest = attach_approved_translation(
            project_id,
            text="Это окончательно утверждённый перевод, готовый к производству.",
            approved_by_user_id=owner,
        )
        manifest["status"] = "ready_for_production"
        manifest["production"]["ready"] = True
        manifest["production"]["profile"] = profile
        manifest["production"]["stage"] = "ready_for_production"
        manifest["preflight"] = {
            "ok": True,
            "checked_at": "2026-07-27T00:00:00Z",
            "translation_contract_sha256": manifest["translation"][
                "contract_sha256"
            ],
        }
        save_project(project_id, manifest)
        return project_id

    return create


def test_enqueue_requires_successful_current_preflight(ready_projects) -> None:
    project_id = ready_projects(owner=1, url="https://example.test/one")
    manifest = load_project(project_id)
    manifest["preflight"]["translation_contract_sha256"] = "stale"
    save_project(project_id, manifest)

    with pytest.raises(job_queue.DubQueueError, match="изменился"):
        job_queue.enqueue_project(project_id, requested_by_user_id=1)


def test_owner_only_can_enqueue(ready_projects) -> None:
    project_id = ready_projects(owner=10, url="https://example.test/two")
    with pytest.raises(Exception, match="владелец"):
        job_queue.enqueue_project(project_id, requested_by_user_id=11)


def test_short_job_has_priority_over_long_job(ready_projects) -> None:
    long_id = ready_projects(
        owner=1,
        url="https://example.test/long",
        profile="long_premium",
    )
    short_id = ready_projects(
        owner=1,
        url="https://example.test/short",
        profile="shorts_premium",
    )
    job_queue.enqueue_project(long_id, requested_by_user_id=1)
    job_queue.enqueue_project(short_id, requested_by_user_id=1)

    first = job_queue.claim_next("worker-a", lease_seconds=60)
    assert first is not None
    assert first.project_id == short_id
    long_job = job_queue.get_job(long_id)
    assert long_job is not None
    assert first.priority > long_job.priority


def test_only_one_worker_claims_a_job(ready_projects) -> None:
    project_id = ready_projects(owner=1, url="https://example.test/claim")
    job_queue.enqueue_project(project_id, requested_by_user_id=1)

    first = job_queue.claim_next("worker-a", lease_seconds=60)
    second = job_queue.claim_next("worker-b", lease_seconds=60)

    assert first is not None
    assert first.project_id == project_id
    assert first.lease_owner == "worker-a"
    assert second is None


def test_heartbeat_requires_current_lease(ready_projects) -> None:
    project_id = ready_projects(owner=1, url="https://example.test/heartbeat")
    job_queue.enqueue_project(project_id, requested_by_user_id=1)
    job_queue.claim_next("worker-a", lease_seconds=60)

    updated = job_queue.heartbeat(
        project_id,
        worker_id="worker-a",
        lease_seconds=120,
        stage="transcribing",
    )
    assert updated.stage == "transcribing"
    assert updated.lease_expires_at is not None
    with pytest.raises(job_queue.DubQueueError, match="Lease"):
        job_queue.heartbeat(project_id, worker_id="worker-b")


def test_expired_lease_is_requeued_and_claimed_by_another_worker(
    ready_projects,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id = ready_projects(owner=1, url="https://example.test/expired")
    job_queue.enqueue_project(project_id, requested_by_user_id=1)
    now = 1000.0
    monkeypatch.setattr(job_queue.time, "time", lambda: now)
    claimed = job_queue.claim_next("worker-a", lease_seconds=30)
    assert claimed is not None

    monkeypatch.setattr(job_queue.time, "time", lambda: now + 31)
    recovered = job_queue.claim_next("worker-b", lease_seconds=60)
    assert recovered is not None
    assert recovered.project_id == project_id
    assert recovered.lease_owner == "worker-b"
    assert recovered.attempts == 2
    assert recovered.stage == "resume"


def test_retryable_failure_returns_job_to_queue(ready_projects) -> None:
    project_id = ready_projects(owner=1, url="https://example.test/retry")
    job_queue.enqueue_project(project_id, requested_by_user_id=1)
    job_queue.claim_next("worker-a")

    failed = job_queue.fail(
        project_id,
        worker_id="worker-a",
        error="temporary source timeout",
        retryable=True,
    )
    assert failed.state == "queued"
    assert failed.stage == "resume"
    assert failed.last_error == "temporary source timeout"

    reclaimed = job_queue.claim_next("worker-b")
    assert reclaimed is not None
    assert reclaimed.attempts == 2


def test_completion_clears_lease(ready_projects) -> None:
    project_id = ready_projects(owner=1, url="https://example.test/complete")
    job_queue.enqueue_project(project_id, requested_by_user_id=1)
    job_queue.claim_next("worker-a")

    completed = job_queue.complete(project_id, worker_id="worker-a")
    assert completed.state == "completed"
    assert completed.lease_owner is None
    assert completed.lease_expires_at is None
    assert job_queue.claim_next("worker-b") is None


def test_duplicate_enqueue_is_idempotent(ready_projects) -> None:
    project_id = ready_projects(owner=1, url="https://example.test/idempotent")
    first = job_queue.enqueue_project(project_id, requested_by_user_id=1)
    second = job_queue.enqueue_project(project_id, requested_by_user_id=1)
    assert first.project_id == second.project_id
    assert second.state == "queued"
    assert len(job_queue.list_jobs(states={"queued"})) == 1
