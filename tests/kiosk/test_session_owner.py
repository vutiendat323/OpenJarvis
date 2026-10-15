"""The departing customer's session must end even when another person stays."""

import asyncio
from contextlib import suppress
from types import SimpleNamespace

import pytest

from openjarvis.core.events import EventBus, EventType
from openjarvis.kiosk import runtime
from openjarvis.kiosk.config import KioskConfig
from openjarvis.kiosk.effects import KioskDependencies, run_side_effects
from openjarvis.kiosk.evaluate import get_config, set_config


def _presence(ts, *tracks):
    rows = [
        {"track_id": tid, "distance_m": distance, "body_m": body}
        for tid, distance, body in tracks
    ]
    nearest = min(rows, key=lambda row: row["distance_m"] or float("inf"))
    return {
        "event": "person_near",
        "ts": ts,
        "track_id": nearest["track_id"],
        "nearest_m": nearest["distance_m"],
        "body_m": min(row["body_m"] for row in rows),
        "facing": True,
        "tracks": rows,
    }


@pytest.mark.parametrize(
    "tracks,kind,distance,body",
    [
        ([(1, 0.8, 0.9), (2, 0.5, 0.6)], "person_near", 0.8, -1.0),
        ([(1, 1.4, 0.9), (2, 0.5, 0.6)], "person_near", 1.4, -1.0),
        ([(2, 0.5, 0.6)], "no_person", 0.0, -1.0),
        ([(1, None, 0.9), (2, 0.5, 0.6)], "person_unknown", 0.0, 0.9),
    ],
)
def test_active_observation_belongs_only_to_the_session_owner(
    tracks, kind, distance, body
):
    event = runtime._dict_to_vision_event(_presence(100, *tracks), session_track_id=1)
    assert (event.kind, event.nearest_m, event.body_m) == (kind, distance, body)
    assert event.track_id == (1 if kind != "no_person" else -1)


@pytest.mark.parametrize("missing", [False, True])
@pytest.mark.parametrize("threshold,leave_seconds", [(1.0, 10.0), (1.5, 3.0)])
def test_customer_departure_ends_session_and_next_customer_starts_fresh(
    monkeypatch, missing, threshold, leave_seconds
):
    async def run():
        queue, evaluated = asyncio.Queue(), asyncio.Queue()
        clock = SimpleNamespace(now=100.0)
        bus = EventBus(record_history=True)
        original = get_config()
        monkeypatch.setattr(runtime, "time", SimpleNamespace(time=lambda: clock.now))
        monkeypatch.setattr(runtime, "_current_state", "idle")
        monkeypatch.setattr(runtime, "_current_response_queue", None)
        monkeypatch.setattr(runtime, "_customer_present", None)

        async def effects(effects, deps):
            await run_side_effects(effects, deps)
            await evaluated.put(runtime.current_state())

        monkeypatch.setattr(runtime, "run_side_effects", effects)
        task = asyncio.create_task(
            runtime.kiosk_main(
                queue,
                KioskDependencies(bus=bus),
                config=KioskConfig(
                    approach_threshold_m=threshold,
                    leave_sustain_seconds_active=leave_seconds,
                ),
            )
        )

        async def step(ts, *tracks):
            clock.now = ts
            await queue.put(_presence(ts, *tracks))
            return await asyncio.wait_for(evaluated.get(), timeout=1)

        a, b = (1, 0.7, 0.8), (2, 0.5, 0.6)
        away = () if missing else ((1, threshold + 0.2, 0.8),)
        try:
            assert await step(100, a) == "idle"
            assert await step(100.4, a) == "approaching"
            assert await step(105, a) == "active"
            # B stays in the zone while A leaves; even a nearer body cannot
            # override A's measured face distance.
            assert await step(105.2, *away, b) == "active"
            assert await step(105 + leave_seconds - 0.1, *away, b) == "active"
            end = 105 + leave_seconds
            assert await step(end, *away, b) == "cleanup"
            assert await step(end + 0.2, b) == "idle"
            assert await step(end + 0.4, b) == "idle"
            assert await step(end + 0.6, b) == "approaching"
            assert await step(end + 6, b) == "active"
            # A cannot keep B's new session alive either.
            assert await step(end + 6.2, a) == "active"
            assert await step(end + 6 + leave_seconds, a) == "cleanup"
            states = [
                event.data
                for event in bus.history
                if event.event_type == EventType.KIOSK_STATE_CHANGED
            ]
            assert [state["state"] for state in states] == [
                "approaching",
                "active",
                "cleanup",
                "idle",
                "approaching",
                "active",
                "cleanup",
            ]
            assert [state["mic_enabled"] for state in states] == [
                False,
                True,
                False,
                False,
                False,
                True,
                False,
            ]
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            set_config(original)

    asyncio.run(run())


def test_customer_return_resets_departure_timer_even_when_bystander_is_nearer(
    monkeypatch,
):
    async def run():
        queue, evaluated = asyncio.Queue(), asyncio.Queue()
        clock = SimpleNamespace(now=100.0)
        original = get_config()
        monkeypatch.setattr(runtime, "time", SimpleNamespace(time=lambda: clock.now))
        monkeypatch.setattr(runtime, "_current_state", "idle")
        monkeypatch.setattr(runtime, "_current_response_queue", None)
        monkeypatch.setattr(runtime, "_customer_present", None)

        async def effects(effects, deps):
            await run_side_effects(effects, deps)
            await evaluated.put(runtime.current_state())

        monkeypatch.setattr(runtime, "run_side_effects", effects)
        task = asyncio.create_task(
            runtime.kiosk_main(
                queue,
                KioskDependencies(),
                config=KioskConfig(),
            )
        )

        async def step(ts, *tracks):
            clock.now = ts
            await queue.put(_presence(ts, *tracks))
            return await asyncio.wait_for(evaluated.get(), timeout=1)

        a, b, away = (1, 0.7, 0.8), (2, 0.5, 0.6), (1, 1.3, 1.4)
        try:
            assert await step(100, a) == "idle"
            assert await step(100.4, a) == "approaching"
            assert await step(105, a) == "active"
            assert await step(106, away, b) == "active"
            assert await step(114, a, b) == "active"
            assert await step(115, away, b) == "active"
            assert await step(123.9, away, b) == "active"
            assert await step(124, away, b) == "cleanup"
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            set_config(original)

    asyncio.run(run())
