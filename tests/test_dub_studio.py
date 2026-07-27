from __future__ import annotations

from pathlib import Path

import pytest

from services.dub_studio import DubStore, list_recipes, load_recipe


def test_piper_recipe_is_registered_and_allowlisted() -> None:
    recipe = load_recipe("john_piper_z20py4yqhyq")
    assert recipe.recipe_id == "john_piper_z20py4yqhyq"
    assert "render" in recipe.actions
    assert "repair_psalm15" in recipe.repair_actions()
    assert recipe.actions["render"]["runner"] == "powershell"


def test_project_queue_and_cancel_are_durable(tmp_path: Path) -> None:
    store = DubStore(tmp_path)
    project = store.create_project(
        "john_piper_z20py4yqhyq",
        owner_user_id=101,
        owner_chat_id=202,
    )
    assert project["status"] == "draft"

    job = store.enqueue_job(project["id"], "render")
    assert job["status"] == "queued"
    assert store.get_project(project["id"])["status"] == "queued"

    cancelled = store.request_cancel(project["id"])
    assert cancelled["status"] == "cancelled"
    assert store.get_project(project["id"])["status"] == "cancelled"

    reopened = DubStore(tmp_path)
    assert reopened.get_job(job["id"])["status"] == "cancelled"


def test_only_one_active_job_per_project(tmp_path: Path) -> None:
    store = DubStore(tmp_path)
    project = store.create_project(
        "john_piper_z20py4yqhyq",
        owner_user_id=1,
        owner_chat_id=1,
    )
    store.enqueue_job(project["id"], "render")
    with pytest.raises(RuntimeError, match="активное задание"):
        store.enqueue_job(project["id"], "repair_psalm15")


def test_worker_claim_progress_and_finish(tmp_path: Path) -> None:
    store = DubStore(tmp_path)
    project = store.create_project(
        "john_piper_z20py4yqhyq",
        owner_user_id=1,
        owner_chat_id=2,
    )
    queued = store.enqueue_job(project["id"], "repair_psalm15")
    claimed = store.claim_next_job("test-worker")
    assert claimed is not None
    assert claimed["id"] == queued["id"]
    assert claimed["status"] == "running"

    store.update_job_progress(
        queued["id"],
        progress=55,
        stage="segment 1/1",
        message="candidate ready",
    )
    assert store.get_project(project["id"])["progress"] == 55

    store.finish_job(
        queued["id"],
        status="succeeded",
        result={"outputs": {"mixed": {"exists": True}}},
    )
    assert store.get_job(queued["id"])["status"] == "succeeded"
    assert store.get_project(project["id"])["status"] == "done"
    events = store.undelivered_terminal_events()
    assert len(events) == 1
    assert events[0]["event_type"] == "job_succeeded"


def test_unknown_recipe_and_action_are_rejected(tmp_path: Path) -> None:
    store = DubStore(tmp_path)
    with pytest.raises((ValueError, FileNotFoundError)):
        store.create_project(
            "../outside",
            owner_user_id=1,
            owner_chat_id=1,
        )

    project = store.create_project(
        "john_piper_z20py4yqhyq",
        owner_user_id=1,
        owner_chat_id=1,
    )
    with pytest.raises(KeyError):
        store.enqueue_job(project["id"], "shell")


def test_recipe_catalog_has_unique_ids() -> None:
    recipes = list_recipes()
    ids = [item.recipe_id for item in recipes]
    assert len(ids) == len(set(ids))
    assert "john_piper_z20py4yqhyq" in ids
