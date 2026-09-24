"""Leave detection must not fire while the customer is still standing there.

Tracks come from faces, so a customer who turns to talk to a friend used to
vanish from the vision stream and the session ended under them. The vision
service now also reports a body distance (YOLO + Metric3D); presence uses it,
engagement still uses the face.
"""

from __future__ import annotations

from openjarvis.kiosk.evaluate import evaluate_state
from openjarvis.kiosk.events import EventHistory, VisionEvent


def _history(*events: VisionEvent) -> EventHistory:
    history = EventHistory()
    for event in events:
        history.push(event)
    return history


def _near(ts: float, m: float = 0.6) -> VisionEvent:
    return VisionEvent(
        kind="person_near", ts=ts, nearest_m=m, track_id=1, body_m=m + 0.1, facing=True
    )


def _turned_away(ts: float, body_m: float = 0.7) -> VisionEvent:
    """No face (so no track), but a body still stands in the zone."""
    return VisionEvent(
        kind="no_person", ts=ts, nearest_m=0.0, track_id=-1, body_m=body_m, facing=False
    )


def _gone(ts: float) -> VisionEvent:
    return VisionEvent(
        kind="no_person", ts=ts, nearest_m=0.0, track_id=-1, body_m=-1.0, facing=False
    )


def _evaluate(state: str, history: EventHistory, now: float):
    return evaluate_state(
        history,
        now,
        state,
        user_response=None,
        session_start=now - 30.0,
        prompting_started_at=now - 30.0,
        last_decline_at=None,
    )


class TestActiveSession:
    def test_turning_away_keeps_the_session(self):
        history = _history(_near(0.0), *[_turned_away(t) for t in range(1, 30)])
        state, _ = _evaluate("active", history, 30.0)
        assert state == "active"

    def test_walking_off_still_ends_the_session(self):
        history = _history(_near(0.0), *[_gone(t) for t in range(1, 30)])
        state, effects = _evaluate("active", history, 30.0)
        assert state == "cleanup"
        assert any(e.kind == "tts_goodbye" for e in effects)

    def test_a_body_beyond_the_zone_does_not_count_as_present(self):
        far = [_turned_away(t, body_m=3.0) for t in range(1, 30)]
        state, _ = _evaluate("active", _history(_near(0.0), *far), 30.0)
        assert state == "cleanup"


class TestApproaching:
    def test_a_back_turned_body_never_starts_a_greeting(self):
        """Greeting needs a face: the customer has to look at the kiosk."""
        history = _history(*[_turned_away(t) for t in range(0, 10)])
        state, effects = evaluate_state(
            history,
            10.0,
            "idle",
            user_response=None,
            session_start=None,
            prompting_started_at=None,
            last_decline_at=None,
        )
        assert state == "idle" and effects == []
