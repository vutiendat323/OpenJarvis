"""Tests for the parallel voice-run tracker."""

from __future__ import annotations

import threading
import time

from openjarvis.core.runs import (
    RunTracker,
    StableOutcome,
    current_voice_run,
    voice_run_scope,
)

C = "conv"


def _later(delay, fn, *args):
    def run():
        time.sleep(delay)
        fn(*args)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


def test_in_flight_lists_other_requests_in_arrival_order():
    t = RunTracker()
    a = t.begin(C, "a")
    b = t.begin(C, "b")
    t.begin(C, "c")
    assert t.in_flight(C, exclude=b) == ["a", "c"]
    t.end(C, a)
    assert t.in_flight(C, exclude=b) == ["c"]


def test_wait_stable_returns_stable_once_snapshot_runs_end():
    t = RunTracker()
    a = t.begin(C, "a")
    b = t.begin(C, "b")
    _later(0.1, t.end, C, a)
    assert t.wait_stable(C, b, 1.0) is StableOutcome.STABLE


def test_wait_stable_times_out_without_ending():
    t = RunTracker()
    t.begin(C, "a")
    b = t.begin(C, "b")
    start = time.monotonic()
    assert t.wait_stable(C, b, 0.2) is StableOutcome.TIMEOUT
    assert time.monotonic() - start >= 0.19


def test_a_new_runs_write_during_the_wait_is_changed():
    t = RunTracker()
    t.begin(C, "a")
    b = t.begin(C, "b")

    def new_run():
        c = t.begin(C, "c")
        t.note_write(C, c)

    _later(0.1, new_run)
    assert t.wait_stable(C, b, 1.0) is StableOutcome.CHANGED


def test_a_snapshot_runs_write_is_not_a_change():
    t = RunTracker()
    a = t.begin(C, "a")
    b = t.begin(C, "b")

    def write_then_end():
        t.note_write(C, a)
        t.end(C, a)

    _later(0.1, write_then_end)
    assert t.wait_stable(C, b, 1.0) is StableOutcome.STABLE


def test_a_new_run_that_only_reads_does_not_change_a_waiting_checkout():
    t = RunTracker()
    a = t.begin(C, "a")
    b = t.begin(C, "b")
    _later(0.05, t.begin, C, "reader")
    _later(0.1, t.end, C, a)
    assert t.wait_stable(C, b, 1.0) is StableOutcome.STABLE


def test_two_waiting_checkouts_ignore_each_other():
    t = RunTracker()
    a = t.begin(C, "a")
    b = t.begin(C, "b")
    out = {}

    def wait(name, rid):
        out[name] = t.wait_stable(C, rid, 0.5)

    ths = [
        threading.Thread(target=wait, args=("a", a)),
        threading.Thread(target=wait, args=("b", b)),
    ]
    for th in ths:
        th.start()
    for th in ths:
        th.join()
    assert out == {"a": StableOutcome.STABLE, "b": StableOutcome.STABLE}


def test_wait_others_waits_only_for_runs_in_flight_at_the_call():
    t = RunTracker()
    a = t.begin(C, "a")
    b = t.begin(C, "b")
    _later(0.05, t.begin, C, "late")
    _later(0.1, t.end, C, a)
    assert t.wait_others(C, b, 1.0) is True
    assert t.wait_others(C, b, 0.1) is False  # "late" never ends


def test_freeze_blocks_other_runs_but_not_its_owner():
    t = RunTracker()
    a = t.begin(C, "a")
    b = t.begin(C, "b")
    assert t.freeze(C, a) is True
    assert t.wait_unfrozen(C, a, 0.1) is True
    assert t.wait_unfrozen(C, b, 0.1) is False
    _later(0.1, t.unfreeze, C, a)
    assert t.wait_unfrozen(C, b, 1.0) is True


def test_freeze_is_exclusive():
    t = RunTracker()
    a = t.begin(C, "a")
    b = t.begin(C, "b")
    assert t.freeze(C, a) is True
    assert t.freeze(C, a) is True  # already owned
    assert t.freeze(C, b) is False
    t.unfreeze(C, b)  # not the owner: no effect
    assert t.freeze(C, b) is False
    t.unfreeze(C, a)
    assert t.freeze(C, b) is True


def test_close_wakes_every_waiter():
    t = RunTracker()
    a = t.begin(C, "a")
    b = t.begin(C, "b")
    d = t.begin(C, "d")
    t.freeze(C, d)
    out = {}
    done = {}

    def run(name, fn):
        out[name] = fn()
        done[name] = time.monotonic()

    ths = [
        threading.Thread(target=run, args=("stable", lambda: t.wait_stable(C, b, 5.0))),
        threading.Thread(target=run, args=("others", lambda: t.wait_others(C, a, 5.0))),
        threading.Thread(
            target=run, args=("unfrozen", lambda: t.wait_unfrozen(C, a, 5.0))
        ),
    ]
    for th in ths:
        th.start()
    time.sleep(0.1)
    closed_at = time.monotonic()
    t.close(C)
    for th in ths:
        th.join(1.0)
    assert out["stable"] is StableOutcome.CLOSED
    assert out["others"] is False
    assert out["unfrozen"] is False
    assert all(done[k] - closed_at < 0.2 for k in done)


def test_voice_run_scope_sets_and_restores():
    assert current_voice_run() == ""
    with voice_run_scope("r1"):
        assert current_voice_run() == "r1"
        with voice_run_scope("r2"):
            assert current_voice_run() == "r2"
        assert current_voice_run() == "r1"
    assert current_voice_run() == ""


def test_empty_ids_are_no_ops():
    t = RunTracker()
    start = time.monotonic()
    assert t.wait_stable("", "", 1) is StableOutcome.STABLE
    assert t.wait_others("", "", 1) is True
    assert t.wait_unfrozen("", "", 1) is True
    assert t.in_flight("") == []
    t.end("", "")
    t.note_write("", "")
    t.unfreeze("", "")
    assert time.monotonic() - start < 0.2
