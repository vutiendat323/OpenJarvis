"""Background schedules may refresh read snapshots and nothing else.

The guard is structural, not prompt-only: a scheduled task cannot choose or
perform a business mutation, and a prohibited task already sitting in the
database from before the guard existed still fails when it comes due.
"""

from __future__ import annotations

import pytest

from openjarvis.scheduler.scheduler import ScheduledTask, TaskScheduler
from openjarvis.scheduler.store import SchedulerStore

pytestmark = pytest.mark.integration

WHEN = "2026-08-20T00:00:00+00:00"


@pytest.fixture
def store(tmp_path):
    store = SchedulerStore(tmp_path / "scheduler.db")
    yield store
    store.close()


@pytest.fixture
def scheduler(store):
    runner = TaskScheduler(store, poll_interval=3600)
    yield runner
    runner.stop()


def test_scheduler_accepts_source_sync(scheduler):
    task = scheduler.create_task(
        prompt="Refresh Trend Coffee menu",
        schedule_type="once",
        schedule_value=WHEN,
        agent="orchestrator",
        tools="source_sync",
    )

    assert task.tools == "source_sync"
    assert [t.tools for t in scheduler.list_tasks()] == ["source_sync"]


def test_scheduler_refuses_every_other_data_plane_tool(scheduler, store):
    for tool in ("source_execute", "source_discover", "source_verify"):
        with pytest.raises(ValueError, match="scheduler_tool_not_allowed"):
            scheduler.create_task(
                prompt="x",
                schedule_type="once",
                schedule_value=WHEN,
                agent="orchestrator",
                tools=tool,
            )

    assert store.list_tasks() == []


def test_scheduler_refuses_a_prohibited_tool_hidden_in_a_longer_list(scheduler):
    with pytest.raises(ValueError, match="scheduler_tool_not_allowed"):
        scheduler.create_task(
            prompt="x",
            schedule_type="once",
            schedule_value=WHEN,
            agent="orchestrator",
            tools="source_sync, source_execute ,web_search",
        )


def test_a_prohibited_task_already_in_the_database_fails_at_execution(scheduler, store):
    prohibited = ScheduledTask(
        id="legacy-1",
        prompt="Pay the outstanding order",
        schedule_type="once",
        schedule_value=WHEN,
        agent="orchestrator",
        tools="source_execute",
    )
    store.save_task(prohibited.to_dict())

    with pytest.raises(ValueError, match="scheduler_tool_not_allowed"):
        scheduler._execute_task(ScheduledTask.from_dict(store.get_task("legacy-1")))

    assert store.get_run_logs("legacy-1") == []


def test_non_data_plane_scheduled_tools_are_unaffected(scheduler):
    task = scheduler.create_task(
        prompt="x",
        schedule_type="once",
        schedule_value=WHEN,
        agent="orchestrator",
        tools="web_search,file_read",
    )

    assert task.tools == "web_search,file_read"
