"""Every dispatched write and its outcome, per conversation, until reported."""

from __future__ import annotations

import logging

from openjarvis.agents.action_ledger import ActionLedger
from openjarvis.core.types import ToolResult

CONV = "conv-1"


def _ok(message: str = "") -> ToolResult:
    metadata = {"customer_message": message} if message else {}
    return ToolResult(tool_name="add", content="ok", success=True, metadata=metadata)


def _started(ledger, call_id="c1", run_id="r1", tool="add"):
    ledger.start(CONV, call_id=call_id, tool=tool, run_id=run_id)


def test_outcomes_map_to_statuses():
    ledger = ActionLedger()
    for call_id, result in [
        ("ok", _ok("Đã thêm.")),
        ("bad", ToolResult(tool_name="add", content="boom", success=False)),
        (
            "unknown",
            ToolResult(
                tool_name="add",
                content="timed out",
                success=False,
                metadata={"outcome_unknown": True},
            ),
        ),
    ]:
        _started(ledger, call_id=call_id)
        ledger.finish(CONV, call_id=call_id, run_id="r1", result=result)

    by_call = {entry.call_id: entry for entry in ledger.entries(CONV)}
    assert by_call["ok"].status == "succeeded"
    assert by_call["ok"].customer_message == "Đã thêm."
    assert by_call["bad"].status == "failed" and by_call["bad"].error == "boom"
    assert by_call["unknown"].status == "unknown"


def test_a_call_that_was_never_dispatched_leaves_no_entry():
    ledger = ActionLedger()
    _started(ledger)
    ledger.finish(
        CONV,
        call_id="c1",
        run_id="r1",
        result=ToolResult(
            tool_name="add",
            content="pending",
            success=False,
            metadata={"pending_operation": True, "dispatched": False},
        ),
    )
    assert ledger.entries(CONV) == []


def test_the_same_call_id_in_two_runs_is_two_entries():
    ledger = ActionLedger()
    _started(ledger, call_id="call_0", run_id="r1")
    _started(ledger, call_id="call_0", run_id="r2")
    assert len(ledger.entries(CONV)) == 2


def test_background_for_lists_other_runs_and_marks_them_seen():
    ledger = ActionLedger()
    _started(ledger, call_id="old", run_id="r1")
    ledger.finish(CONV, call_id="old", run_id="r1", result=_ok("Đã thêm."))
    _started(ledger, call_id="still", run_id="r1")
    _started(ledger, call_id="own", run_id="r2")

    listed = ledger.background_for(CONV, "r2")

    assert listed == [
        {"tool": "add", "status": "succeeded", "message": "Đã thêm."},
        {"tool": "add", "status": "running"},
    ]
    seen = {e.call_id: e.seen_by_run for e in ledger.entries(CONV)}
    assert seen == {"old": "r2", "still": "", "own": ""}


def test_a_write_seen_running_is_not_reported_when_it_finishes_before_the_run_ends():
    ledger = ActionLedger()
    _started(ledger, call_id="w", run_id="r1")
    listed = ledger.background_for(CONV, "r2")
    ledger.finish(CONV, call_id="w", run_id="r1", result=_ok("Đã thêm."))

    ledger.mark_run_completed(CONV, "r2")

    assert listed == [{"tool": "add", "status": "running"}]
    [entry] = ledger.entries(CONV)
    assert entry.reported is False
    assert [e.call_id for e in ledger.unreported_finished(CONV)] == ["w"]


def test_completed_run_reports_what_it_saw_and_its_own_writes():
    ledger = ActionLedger()
    _started(ledger, call_id="old", run_id="r1")
    ledger.finish(CONV, call_id="old", run_id="r1", result=_ok())
    _started(ledger, call_id="running", run_id="r1")
    _started(ledger, call_id="own", run_id="r2")
    ledger.finish(CONV, call_id="own", run_id="r2", result=_ok())
    ledger.background_for(CONV, "r2")

    ledger.mark_run_completed(CONV, "r2")

    reported = {e.call_id: e.reported for e in ledger.entries(CONV)}
    assert reported == {"old": True, "running": False, "own": True}


def test_unreported_finished_is_oldest_first_and_skips_running():
    ledger = ActionLedger()
    for call_id in ("a", "b", "c"):
        _started(ledger, call_id=call_id)
    ledger.finish(CONV, call_id="b", run_id="r1", result=_ok())
    ledger.finish(CONV, call_id="a", run_id="r1", result=_ok())

    assert [e.call_id for e in ledger.unreported_finished(CONV)] == ["a", "b"]
    ledger.mark_reported(CONV, ledger.entries(CONV)[0].key, via="reporter")
    assert [e.call_id for e in ledger.unreported_finished(CONV)] == ["b"]


def test_listeners_hear_each_finished_entry_once():
    ledger = ActionLedger()
    heard = []
    unsubscribe = ledger.subscribe(CONV, heard.append)
    _started(ledger)
    ledger.finish(CONV, call_id="c1", run_id="r1", result=_ok())
    unsubscribe()
    _started(ledger, call_id="c2")
    ledger.finish(CONV, call_id="c2", run_id="r1", result=_ok())

    assert [entry.call_id for entry in heard] == ["c1"]


def test_a_failing_listener_does_not_stop_the_others_or_finish(caplog):
    ledger = ActionLedger()
    heard = []

    def broken(entry):
        raise RuntimeError("loop closed")

    ledger.subscribe(CONV, broken)
    ledger.subscribe(CONV, heard.append)
    with caplog.at_level(logging.WARNING, logger="openjarvis.agents.action_ledger"):
        _started(ledger)
        ledger.finish(CONV, call_id="c1", run_id="r1", result=_ok())
        ledger.mark_run_completed(CONV, "r1")

    assert [entry.call_id for entry in heard] == ["c1"]
    [entry] = ledger.entries(CONV)
    assert entry.status == "succeeded"
    assert (
        sum(
            "background_action_listener_failed" in r.getMessage()
            for r in caplog.records
        )
        == 1
    )
    # Ids only, so a live audit can match each call's start, end and report.
    messages = [r.getMessage() for r in caplog.records]
    assert "background_action_started tool=add run=r1 call=c1" in messages
    assert (
        "background_action_finished tool=add status=succeeded run=r1 call=c1"
        in messages
    )
    assert "background_action_reported via=model tool=add run=r1 call=c1" in messages


def test_a_conversation_keeps_its_newest_entries_only():
    ledger = ActionLedger()
    for index in range(ActionLedger.MAX_ENTRIES + 5):
        _started(ledger, call_id=f"c{index}")
    entries = ledger.entries(CONV)
    assert len(entries) == ActionLedger.MAX_ENTRIES
    assert entries[0].call_id == "c5"


def test_finish_after_clear_is_logged_and_ignored(caplog):
    ledger = ActionLedger()
    _started(ledger)
    ledger.clear(CONV)
    with caplog.at_level(logging.WARNING, logger="openjarvis.agents.action_ledger"):
        ledger.finish(CONV, call_id="c1", run_id="r1", result=_ok())

    assert ledger.entries(CONV) == []
    assert any(
        "background_action_after_session_end" in r.getMessage() for r in caplog.records
    )


def test_old_conversations_are_evicted():
    ledger = ActionLedger()
    for index in range(ActionLedger.MAX_CONVERSATIONS + 1):
        ledger.start(f"conv-{index}", call_id="c", tool="add", run_id="r")
    assert ledger.entries("conv-0") == []
    assert len(ledger.entries(f"conv-{ActionLedger.MAX_CONVERSATIONS}")) == 1


def _failed(content: str = "merchant refused", **metadata) -> ToolResult:
    return ToolResult(
        tool_name="add", content=content, success=False, metadata=metadata
    )


def test_unseen_failures_are_other_runs_failed_or_unknown_outcomes_not_yet_seen():
    ledger = ActionLedger()
    for call_id, result in [
        ("ok", _ok()),
        ("bad", _failed()),
        ("lost", _failed("timed out", outcome_unknown=True)),
    ]:
        _started(ledger, call_id=call_id, run_id="r1")
        ledger.finish(CONV, call_id=call_id, run_id="r1", result=result)
    _started(ledger, call_id="running", run_id="r1")
    _started(ledger, call_id="own", run_id="r2")
    ledger.finish(CONV, call_id="own", run_id="r2", result=_failed())

    unseen = ledger.unseen_failures(CONV, "r2")

    assert [(e.call_id, e.status) for e in unseen] == [
        ("bad", "failed"),
        ("lost", "unknown"),
    ]
    # Once the run's model has read them, they no longer block its writes.
    ledger.background_for(CONV, "r2")
    assert ledger.unseen_failures(CONV, "r2") == []
    later = ledger.unseen_failures(CONV, "r3")
    assert [e.call_id for e in later] == ["bad", "lost", "own"]
    ledger.mark_reported(CONV, unseen[0].key, via="reporter")
    assert [e.call_id for e in ledger.unseen_failures(CONV, "r3")] == ["lost", "own"]
    assert ledger.unseen_failures("other-conversation", "r3") == []


def _told(ledger, call_id, run_id="r1", message="Đã thêm."):
    _started(ledger, call_id=call_id, run_id=run_id)
    ledger.finish(CONV, call_id=call_id, run_id=run_id, result=_ok(message))
    key = next(e.key for e in ledger.entries(CONV) if e.call_id == call_id)
    ledger.mark_reported(CONV, key, via="reporter")


def test_a_carried_request_still_sees_what_it_already_told():
    """Spec §5.5: what the carried request started stays in background_actions."""
    ledger = ActionLedger()
    _told(ledger, "told")
    _told(ledger, "own", run_id="r2")
    assert ledger.background_for(CONV, "r2") == []

    ledger.carry(CONV)

    assert ledger.background_for(CONV, "r2") == [
        {
            "tool": "add",
            "status": "succeeded",
            "message": "Đã thêm.",
            "already_told": "true",
        }
    ]
    assert {e.call_id: e.seen_by_run for e in ledger.entries(CONV)} == {
        "told": "",
        "own": "",
    }


def test_settling_ends_the_carry_and_retires_what_was_told():
    ledger = ActionLedger()
    _told(ledger, "told")
    ledger.carry(CONV)
    _started(ledger, call_id="later", run_id="r2")
    ledger.finish(CONV, call_id="later", run_id="r2", result=_ok("Đã thêm trà."))

    ledger.settle(CONV)

    assert all(e.settled for e in ledger.entries(CONV))
    # Unreported outcomes are still listed; told ones are not, even if carried
    # again later.
    assert ledger.background_for(CONV, "r3") == [
        {"tool": "add", "status": "succeeded", "message": "Đã thêm trà."}
    ]
    ledger.carry(CONV)
    assert ledger.background_for(CONV, "r4") == [
        {"tool": "add", "status": "succeeded", "message": "Đã thêm trà."}
    ]


def test_clear_ends_a_carry():
    ledger = ActionLedger()
    ledger.carry(CONV)
    ledger.clear(CONV)
    _told(ledger, "told")

    assert ledger.background_for(CONV, "r2") == []


# --- interrupted runs (spec 2026-10-17 barge-in §5.6) -----------------------


def test_interrupted_run_flags_existing_and_later_entries():
    ledger = ActionLedger()
    ledger.start("c", call_id="a", tool="add", run_id="r1")
    ledger.mark_run_interrupted("c", "r1")
    ledger.start("c", call_id="b", tool="checkout", run_id="r1")
    ledger.start("c", call_id="x", tool="add", run_id="r2")
    flags = {e.call_id: e.interrupted for e in ledger.entries("c")}
    assert flags == {"a": True, "b": True, "x": False}


def test_a_skill_refused_before_it_started_leaves_no_entry():
    ledger = ActionLedger()
    ledger.start("c", call_id="a", tool="skill_checkout", run_id="r1")
    ledger.finish(
        "c",
        call_id="a",
        run_id="r1",
        result=ToolResult(
            tool_name="skill_checkout",
            content="turn nonce is stale or consumed",
            success=False,
            metadata={"execution_started": False},
        ),
    )
    assert ledger.entries("c") == []


def test_clear_forgets_interrupted_runs():
    ledger = ActionLedger()
    ledger.mark_run_interrupted("c", "r1")
    ledger.clear("c")
    ledger.start("c", call_id="a", tool="add", run_id="r1")
    assert ledger.entries("c")[0].interrupted is False


# --- run-level entries (spec 2026-10-18 parallel agent runs) ---------------


def test_a_run_entry_is_listed_for_other_runs_with_its_request_and_answer():
    ledger = ActionLedger()
    ledger.start_run(CONV, "r1", "cho mình một ly trà")
    assert ledger.background_for(CONV, "r2") == [
        {"request": "cho mình một ly trà", "status": "running"}
    ]

    ledger.finish_run(CONV, "r1", answer="Đã thêm trà.", status="succeeded")

    assert ledger.background_for(CONV, "r2") == [
        {
            "request": "cho mình một ly trà",
            "status": "succeeded",
            "answer": "Đã thêm trà.",
        }
    ]
    [entry] = ledger.entries(CONV)
    assert (entry.key, entry.call_id, entry.tool) == ("r1:run", "run", "agent_run")
    assert entry.kind == "run" and entry.request == "cho mình một ly trà"
    assert entry.seen_by_run == "r2"
    # The run's own context lists nothing of itself.
    assert ledger.background_for(CONV, "r1") == []


def test_a_finished_run_without_writes_is_still_seen():
    ledger = ActionLedger()
    ledger.start_run(CONV, "r1", "hỏi giờ mở cửa")
    ledger.finish_run(CONV, "r1", answer="Tám giờ.", status="succeeded")

    assert [e.key for e in ledger.unreported_finished(CONV)] == ["r1:run"]
    ledger.background_for(CONV, "r2")
    ledger.mark_run_completed(CONV, "r2")

    assert ledger.unreported_finished(CONV) == []
    [entry] = ledger.entries(CONV)
    assert entry.reported is True


def test_folding_one_entry_reports_the_whole_run():
    ledger = ActionLedger()
    ledger.start_run(CONV, "r1", "thêm hai món")
    _started(ledger, call_id="a", run_id="r1")
    _started(ledger, call_id="b", run_id="r1")
    ledger.finish(CONV, call_id="a", run_id="r1", result=_ok())
    # r2's model reads only "a"; the rest of r1 finishes afterwards.
    ledger.background_for(CONV, "r2")
    ledger.finish(CONV, call_id="b", run_id="r1", result=_ok())
    ledger.finish_run(CONV, "r1", answer="Xong.", status="succeeded")
    _started(ledger, call_id="other", run_id="r3")
    ledger.finish(CONV, call_id="other", run_id="r3", result=_ok())

    ledger.mark_run_completed(CONV, "r2")

    reported = {e.key: e.reported for e in ledger.entries(CONV)}
    assert reported == {
        "r1:run": True,
        "r1:a": True,
        "r1:b": True,
        "r3:other": False,
    }


def test_a_still_running_tool_of_a_folded_run_is_not_swallowed():
    ledger = ActionLedger()
    _started(ledger, call_id="a", run_id="r1")
    _started(ledger, call_id="b", run_id="r1")
    ledger.finish(CONV, call_id="a", run_id="r1", result=_ok())
    ledger.background_for(CONV, "r2")

    ledger.mark_run_completed(CONV, "r2")

    assert {e.call_id: e.reported for e in ledger.entries(CONV)} == {
        "a": True,
        "b": False,
    }


def test_mark_run_reported_marks_every_entry_of_the_run(caplog):
    ledger = ActionLedger()
    ledger.start_run(CONV, "r1", "thêm hai món")
    _started(ledger, call_id="a", run_id="r1")
    ledger.finish(CONV, call_id="a", run_id="r1", result=_ok())
    ledger.finish_run(CONV, "r1", answer="Xong.", status="succeeded")
    _started(ledger, call_id="other", run_id="r2")
    ledger.finish(CONV, call_id="other", run_id="r2", result=_ok())

    with caplog.at_level(logging.WARNING, logger="openjarvis.agents.action_ledger"):
        ledger.mark_run_reported(CONV, "r1", via="reporter")

    assert {e.key: e.reported for e in ledger.entries(CONV)} == {
        "r1:run": True,
        "r1:a": True,
        "r2:other": False,
    }
    assert [e.key for e in ledger.unreported_finished(CONV)] == ["r2:other"]
    # Idempotent.
    ledger.mark_run_reported(CONV, "r1", via="reporter")
    messages = [r.getMessage() for r in caplog.records]
    assert (
        sum(m.startswith("background_action_reported via=reporter") for m in messages)
        == 2
    )


def test_mark_run_reported_leaves_a_running_entry_to_be_reported_after_it_finishes():
    ledger = ActionLedger()
    ledger.start_run(CONV, "r1", "thêm hai món")
    _started(ledger, call_id="done", run_id="r1")
    ledger.finish(CONV, call_id="done", run_id="r1", result=_ok())
    _started(ledger, call_id="slow", run_id="r1")
    ledger.finish_run(CONV, "r1", answer="Xong.", status="succeeded")

    ledger.mark_run_reported(CONV, "r1", via="answered")

    assert {e.key: e.reported for e in ledger.entries(CONV)} == {
        "r1:run": True,
        "r1:done": True,
        "r1:slow": False,
    }
    ledger.finish(CONV, call_id="slow", run_id="r1", result=_ok())
    assert [e.key for e in ledger.unreported_finished(CONV)] == ["r1:slow"]


def test_run_group_returns_copies_of_the_run_entry_and_its_tools():
    ledger = ActionLedger()
    assert ledger.run_group(CONV, "r1") == (None, [])
    ledger.start_run(CONV, "r1", "thêm trà")
    _started(ledger, call_id="a", run_id="r1")
    _started(ledger, call_id="x", run_id="r2")

    run, tools = ledger.run_group(CONV, "r1")

    assert run is not None and run.kind == "run"
    assert [t.call_id for t in tools] == ["a"]
    run.reported = True
    tools[0].reported = True
    assert [e.reported for e in ledger.entries(CONV)] == [False, False, False]


def test_run_entries_never_hold_writes_under_d4():
    ledger = ActionLedger()
    ledger.start_run(CONV, "r1", "huỷ đơn")
    ledger.finish_run(CONV, "r1", answer="Đã dừng.", status="stopped")
    assert ledger.unseen_failures(CONV, "r2") == []

    _started(ledger, call_id="bad", run_id="r1")
    ledger.finish(CONV, call_id="bad", run_id="r1", result=_failed())
    assert [e.call_id for e in ledger.unseen_failures(CONV, "r2")] == ["bad"]


def test_a_stopped_run_inherits_interrupted_and_is_phrased_context():
    ledger = ActionLedger()
    ledger.mark_run_interrupted(CONV, "r1")
    ledger.start_run(CONV, "r1", "thêm trà")
    ledger.finish_run(CONV, "r1", answer="", status="stopped")

    [entry] = ledger.entries(CONV)
    assert entry.interrupted is True
    assert entry.as_context() == {"request": "thêm trà", "status": "stopped"}


def test_finish_run_notifies_listeners(caplog):
    ledger = ActionLedger()
    heard = []
    ledger.subscribe(CONV, heard.append)
    ledger.start_run(CONV, "r1", "thêm trà")

    with caplog.at_level(logging.WARNING, logger="openjarvis.agents.action_ledger"):
        ledger.finish_run(CONV, "r1", answer="Xong.", status="succeeded")

    [entry] = heard
    assert entry.kind == "run" and entry.status == "succeeded"
    assert entry.customer_message == "Xong."
    assert "background_run_finished status=succeeded run=r1" in [
        r.getMessage() for r in caplog.records
    ]


def test_finish_run_after_clear_is_ignored():
    ledger = ActionLedger()
    ledger.start_run(CONV, "r1", "thêm trà")
    ledger.clear(CONV)
    ledger.finish_run(CONV, "r1", answer="Xong.", status="succeeded")
    assert ledger.entries(CONV) == []


def test_mark_run_reported_with_keys_marks_only_those_entries():
    ledger = ActionLedger()
    for call_id in ("a", "b"):
        _started(ledger, call_id=call_id, run_id="r1")
        ledger.finish(CONV, call_id=call_id, run_id="r1", result=_ok())

    ledger.mark_run_reported(CONV, "r1", via="reporter", keys={"r1:a"})

    assert [e.key for e in ledger.unreported_finished(CONV)] == ["r1:b"]
