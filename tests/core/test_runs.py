"""Tests for the parallel voice-run tracker."""

from __future__ import annotations

import threading
import time

import pytest

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

    def read_then_end():
        reader = t.begin(C, "reader")
        time.sleep(0.1)
        t.end(C, reader)

    _later(0.05, read_then_end)
    _later(0.1, t.end, C, a)
    assert t.wait_stable(C, b, 1.0) is StableOutcome.STABLE


def test_wait_stable_also_waits_for_a_run_begun_during_the_wait():
    """Ruling 16: a request still thinking when the snapshot ends holds the wait."""
    t = RunTracker()
    a = t.begin(C, "a")
    b = t.begin(C, "b")
    late = {}

    def begin_late():
        late["c"] = t.begin(C, "c")

    _later(0.05, begin_late)
    _later(0.1, t.end, C, a)  # the snapshot is over; "c" is still thinking
    started = time.monotonic()
    assert t.wait_stable(C, b, 0.4) is StableOutcome.TIMEOUT
    assert time.monotonic() - started >= 0.39
    t.end(C, late["c"])


def test_a_late_run_that_ends_without_writing_lets_the_wait_end_stable():
    t = RunTracker()
    a = t.begin(C, "a")
    b = t.begin(C, "b")
    late = {}

    def begin_late():
        late["c"] = t.begin(C, "c")

    _later(0.05, begin_late)
    _later(0.1, t.end, C, a)
    _later(0.25, lambda: t.end(C, late["c"]))
    started = time.monotonic()
    assert t.wait_stable(C, b, 2.0) is StableOutcome.STABLE
    assert time.monotonic() - started >= 0.24


def test_a_late_run_that_writes_after_the_snapshot_ended_is_changed():
    """The C1 shape: A ends first, then the late request C writes."""
    t = RunTracker()
    a = t.begin(C, "a")
    b = t.begin(C, "b")
    late = {}

    def begin_late():
        late["c"] = t.begin(C, "c")

    def write_late():
        t.note_write(C, late["c"])

    _later(0.05, begin_late)
    _later(0.1, t.end, C, a)
    _later(0.2, write_late)
    assert t.wait_stable(C, b, 2.0) is StableOutcome.CHANGED
    t.end(C, late["c"])


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


def test_wait_others_waits_only_for_runs_begun_before_the_caller():
    t = RunTracker()
    a = t.begin(C, "a")
    b = t.begin(C, "b")
    _later(0.05, t.begin, C, "late")
    _later(0.1, t.end, C, a)
    started = time.monotonic()
    assert t.wait_others(C, b, 1.0) is True
    assert time.monotonic() - started >= 0.09
    # "late" never ends, but it began after the caller: not waited for.
    assert t.wait_others(C, b, 0.1) is True


def test_two_corrections_in_a_row_do_not_wait_for_each_other():
    """I4: the later correction waits for the earlier one, never the reverse."""
    t = RunTracker()
    first = t.begin(C, "remove the smoothies")
    second = t.begin(C, "and the coffee too")
    out = {}

    def wait(name, rid, timeout):
        out[name] = (t.wait_others(C, rid, timeout), time.monotonic())

    started = time.monotonic()
    earlier = threading.Thread(target=wait, args=("first", first, 2.0))
    later = threading.Thread(target=wait, args=("second", second, 2.0))
    earlier.start()
    later.start()
    earlier.join()
    assert out["first"][0] is True and out["first"][1] - started < 0.2
    t.end(C, first)
    later.join()
    assert out["second"][0] is True and out["second"][1] - started < 0.5


def test_a_correction_does_not_wait_for_a_waiting_checkout():
    """I4: the checkout waits for the correction; the correction must not wait."""
    t = RunTracker()
    checkout = t.begin(C, "pay")
    correction = t.begin(C, "no smoothies")
    out = {}

    def wait_checkout():
        out["checkout"] = t.wait_stable(C, checkout, 2.0)

    waiter = threading.Thread(target=wait_checkout)
    waiter.start()
    time.sleep(0.05)
    started = time.monotonic()
    assert t.wait_others(C, correction, 2.0) is True
    assert time.monotonic() - started < 0.2
    t.note_write(C, correction)
    t.end(C, correction)
    waiter.join()
    # The correction began before the checkout waited: it is in the snapshot.
    assert out["checkout"] is StableOutcome.STABLE


@pytest.mark.parametrize("frozen", [False, True])
def test_a_correction_waits_for_a_checkout_that_finished_waiting(frozen):
    """N1: a checkout that has left wait_stable may already be ordering."""
    t = RunTracker()
    checkout = t.begin(C, "pay")
    assert t.wait_stable(C, checkout, 0.0) is StableOutcome.STABLE
    if frozen:
        assert t.freeze(C, checkout)
    correction = t.begin(C, "no smoothies")

    assert t.wait_others(C, correction, 0.0) is False
    if frozen:
        t.unfreeze(C, checkout)
        assert t.wait_others(C, correction, 0.0) is False
    t.end(C, checkout)
    assert t.wait_others(C, correction, 0.0) is True


def test_a_correction_waits_for_a_checkout_that_timed_out_waiting():
    t = RunTracker()
    checkout = t.begin(C, "pay")
    adding = t.begin(C, "add tea")
    assert t.wait_stable(C, checkout, 0.0) is StableOutcome.TIMEOUT
    correction = t.begin(C, "no tea")
    t.end(C, adding)

    assert t.wait_others(C, correction, 0.0) is False
    t.end(C, checkout)
    assert t.wait_others(C, correction, 0.0) is True


def test_a_correction_begun_during_a_checkout_wait_drops_it_without_waiting():
    t = RunTracker()
    a = t.begin(C, "add smoothies")
    checkout = t.begin(C, "pay")
    out = {}

    def wait_checkout():
        out["checkout"] = t.wait_stable(C, checkout, 2.0)

    waiter = threading.Thread(target=wait_checkout)
    waiter.start()
    time.sleep(0.05)
    correction = t.begin(C, "no smoothies")
    t.end(C, a)
    started = time.monotonic()
    assert t.wait_others(C, correction, 2.0) is True
    assert time.monotonic() - started < 0.2
    t.note_write(C, correction)
    waiter.join()
    assert out["checkout"] is StableOutcome.CHANGED
    t.end(C, correction)


def test_a_waiting_checkout_wakes_a_peer_as_soon_as_it_waits():
    """Minor 2: entering the wait notifies, so a peer does not sleep to timeout."""
    t = RunTracker()
    b1 = t.begin(C, "pay")
    b2 = t.begin(C, "pay again")
    out = {}

    def wait():
        out["b1"] = (t.wait_stable(C, b1, 3.0), time.monotonic())

    waiter = threading.Thread(target=wait)
    waiter.start()
    time.sleep(0.1)
    started = time.monotonic()
    assert t.wait_stable(C, b2, 3.0) is StableOutcome.STABLE
    waiter.join()
    assert out["b1"][0] is StableOutcome.STABLE
    assert out["b1"][1] - started < 0.5


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
        # d began after a: it waits for a (b is a waiting checkout).
        threading.Thread(target=run, args=("others", lambda: t.wait_others(C, d, 5.0))),
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


def test_calls_after_close_find_it_closed_and_leave_no_state():
    """Minor 1: a late call of a closed session never reopens it."""
    t = RunTracker()
    a = t.begin(C, "a")
    t.close(C)
    start = time.monotonic()
    assert t.wait_stable(C, a, 1.0) is StableOutcome.CLOSED
    assert t.wait_others(C, a, 1.0) is False
    assert t.wait_unfrozen(C, a, 1.0) is False
    assert t.freeze(C, a) is False
    late = t.begin(C, "late")
    t.note_write(C, late)
    assert t.in_flight(C) == [] and t.in_flight_ids(C) == set()
    assert time.monotonic() - start < 0.2
    assert C not in t._convs


def test_closed_ids_are_remembered_up_to_a_bound():
    t = RunTracker()
    t.MAX_CLOSED = 3
    for number in range(5):
        t.close(f"c{number}")
    assert list(t._closed) == ["c2", "c3", "c4"]
    assert t.wait_stable("c4", "r", 0.1) is StableOutcome.CLOSED


def test_a_checkout_freeze_released_since_the_wait_began_supersedes_it():
    """Ruling 17: the epoch moves when a checkout's freeze ends."""
    t = RunTracker()
    b1 = t.begin(C, "pay")
    assert t.wait_stable(C, b1, 0.5) is StableOutcome.STABLE
    assert t.freeze(C, b1)  # b1 is ordering
    b2 = t.begin(C, "pay again")
    assert t.wait_stable(C, b2, 0.5) is StableOutcome.STABLE
    assert t.superseded(C, b2) is False
    t.unfreeze(C, b1)
    assert t.freeze(C, b2)
    assert t.superseded(C, b2) is True


def test_a_freeze_ended_by_its_runs_end_moves_the_epoch_too():
    t = RunTracker()
    b1 = t.begin(C, "pay")
    b2 = t.begin(C, "pay again")
    assert t.wait_stable(C, b2, 0.0) is StableOutcome.TIMEOUT  # b1 still runs
    assert t.freeze(C, b1)
    t.end(C, b1)
    assert t.superseded(C, b2) is True


@pytest.mark.parametrize("end_first", [False, True])
def test_a_peer_release_before_the_checkout_wait_still_supersedes_it(end_first):
    """C2 residual: B2 begins during B1's order but calls checkout after it."""
    t = RunTracker()
    b1 = t.begin(C, "pay")
    assert t.wait_stable(C, b1, 0.0) is StableOutcome.STABLE
    assert t.freeze(C, b1)
    b2 = t.begin(C, "pay again")
    if end_first:
        t.end(C, b1)
    else:
        t.unfreeze(C, b1)

    for _ in range(2):  # retrying the wait must not reset the run's epoch
        assert t.wait_stable(C, b2, 0.0) is StableOutcome.STABLE
        assert t.freeze(C, b2)
        assert t.superseded(C, b2) is True
        t.unfreeze(C, b2)


def test_a_runs_own_freeze_releases_do_not_supersede_it():
    t = RunTracker()
    checkout = t.begin(C, "pay")
    for _ in range(2):
        assert t.wait_stable(C, checkout, 0.0) is StableOutcome.STABLE
        assert t.freeze(C, checkout)
        assert t.superseded(C, checkout) is False
        t.unfreeze(C, checkout)
        assert t.superseded(C, checkout) is False


def test_a_checkout_begun_after_another_ended_is_not_superseded():
    t = RunTracker()
    b1 = t.begin(C, "pay")
    assert t.freeze(C, b1)
    t.end(C, b1)
    b2 = t.begin(C, "pay for a new order")
    assert t.wait_stable(C, b2, 0.5) is StableOutcome.STABLE
    assert t.freeze(C, b2)
    assert t.superseded(C, b2) is False
    assert t.waited_for(C, b2) == frozenset()
    t.end(C, b2)
    assert t.superseded(C, b2) is False  # its record ended with it
    t.close(C)
    assert t.superseded(C, b2) is True  # nothing is ordered after the end


def test_a_run_remembers_the_requests_in_flight_when_it_began():
    """I5: what its prompt named as in progress, by run id."""
    t = RunTracker()
    a = t.begin(C, "add smoothies")
    rejected = t.begin(C, "")  # not named: it has no words
    b = t.begin(C, "did you add them?")
    assert t.named_at_begin(C, b) == frozenset({a})
    assert t.named_at_begin(C, a) == frozenset()
    t.end(C, a)
    assert t.named_at_begin(C, b) == frozenset({a})  # fixed at its begin
    t.end(C, b)
    assert t.named_at_begin(C, b) == frozenset()
    t.end(C, rejected)
