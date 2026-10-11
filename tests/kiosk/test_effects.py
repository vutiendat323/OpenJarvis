"""Tests for Kiosk FSM side effects."""

from __future__ import annotations

import asyncio
import threading

from openjarvis.core.events import EventBus, EventType
from openjarvis.kiosk.config import KioskConfig
from openjarvis.kiosk.effects import KioskDependencies, SideEffect, run_side_effects
from openjarvis.kiosk.evaluate import evaluate_state, get_config, set_config
from openjarvis.kiosk.events import EventHistory, VisionEvent


def test_timeout_speaks_before_publishing_microphone_shutdown() -> None:
    order = []
    bus = EventBus()
    bus.subscribe(EventType.KIOSK_STATE_CHANGED, lambda _: order.append("mic-off"))

    async def speech(_):
        await asyncio.sleep(0)
        order.append("goodbye-finished")

    state, effects = evaluate_state(
        EventHistory(),
        now=600,
        current_state="active",
        user_response=None,
        session_start=0,
        prompting_started_at=None,
    )
    assert state == "cleanup"
    asyncio.run(run_side_effects(effects, KioskDependencies(bus=bus, tts=speech)))
    assert order == ["goodbye-finished", "mic-off"]


def test_publish_state_emits_kiosk_state_event() -> None:
    bus = EventBus(record_history=True)

    asyncio.run(
        run_side_effects(
            [SideEffect("publish_state", {"state": "prompting", "mic_enabled": False})],
            KioskDependencies(bus=bus),
        )
    )

    assert len(bus.history) == 1
    assert bus.history[0].event_type == EventType.KIOSK_STATE_CHANGED
    assert bus.history[0].data == {"state": "prompting", "mic_enabled": False}


def test_accepting_the_kiosk_prompt_starts_voice_before_loading_the_menu() -> None:
    state, effects = evaluate_state(
        EventHistory(),
        now=0.0,
        current_state="prompting",
        user_response="accept",
        session_start=None,
        prompting_started_at=0.0,
    )

    assert state == "active"
    assert effects == [
        SideEffect("publish_state", {"state": "active", "mic_enabled": True}),
        SideEffect("tts_greeting"),
        SideEffect("load_initial_display"),
    ]


def test_customer_in_zone_for_five_seconds_starts_voice_without_a_prompt() -> None:
    original = get_config()
    set_config(KioskConfig())
    try:
        history = EventHistory()
        history.push(VisionEvent("person_near", 0.0, 0.6, 1))
        history.push(VisionEvent("person_near", 4.9, 0.6, 1))
        assert evaluate_state(history, 4.9, "approaching", None, None, None) == (
            "approaching",
            [],
        )
        history.push(VisionEvent("person_near", 5.0, 0.6, 1))
        assert evaluate_state(history, 5.0, "approaching", None, None, None) == (
            "active",
            [
                SideEffect("publish_state", {"state": "active", "mic_enabled": True}),
                SideEffect("tts_greeting"),
                SideEffect("load_initial_display"),
            ],
        )
    finally:
        set_config(original)


def test_voice_can_start_while_initial_menu_is_loading() -> None:
    async def run() -> None:
        bus = EventBus(record_history=True)
        started = asyncio.Event()
        release = threading.Event()
        loop = asyncio.get_running_loop()
        spoken: list[str] = []

        class Presentation:
            def load_initial_display(self) -> None:
                loop.call_soon_threadsafe(started.set)
                release.wait(timeout=1)

        async def speech(text: str) -> None:
            spoken.append(text)

        effects = [
            SideEffect("publish_state", {"state": "active", "mic_enabled": True}),
            SideEffect("tts_greeting"),
            SideEffect("load_initial_display"),
        ]
        task = asyncio.create_task(
            run_side_effects(
                effects,
                KioskDependencies(bus=bus, tts=speech, presentation=Presentation()),
            )
        )
        try:
            await asyncio.wait_for(started.wait(), timeout=2)
            assert not task.done()
            assert bus.history[0].data == {"state": "active", "mic_enabled": True}
            assert len(spoken) == 1
        finally:
            release.set()
            await task

    asyncio.run(run())


def test_publish_display_scopes_menu_to_the_presentation_session() -> None:
    bus = EventBus(record_history=True)

    class Presentation:
        def publish(self, payload: dict) -> None:
            bus.publish(
                EventType.DISPLAY_UPDATE,
                {**payload, "presentation_session_id": "session-1"},
            )

    asyncio.run(
        run_side_effects(
            [SideEffect("publish_display", {"view": "menu", "items": []})],
            KioskDependencies(bus=bus, presentation=Presentation()),
        )
    )

    assert bus.history[0].event_type == EventType.DISPLAY_UPDATE
    assert bus.history[0].data == {
        "view": "menu",
        "items": [],
        "presentation_session_id": "session-1",
    }


def test_load_initial_display_runs_the_configured_menu_recipe() -> None:
    calls: list[str] = []

    class Presentation:
        def load_initial_display(self) -> None:
            calls.append("menu")

    asyncio.run(
        run_side_effects(
            [SideEffect("load_initial_display")],
            KioskDependencies(presentation=Presentation()),
        )
    )

    assert calls == ["menu"]
