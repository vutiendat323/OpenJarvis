"""Identical wire assertions in both repositories; no cross-repo imports."""

import base64
import json
from pathlib import Path


def test_shared_asd_protocol():
    data = json.loads(
        (Path(__file__).parent / "fixtures/asd_protocol_v1.json").read_text()
    )
    start, packet, face = data["start"], data["packet"], data["faces"]
    assert (
        start["cmd"],
        start["version"],
        start["sample_rate"],
        start["channels"],
    ) == ("asd_audio_start", 1, 16000, 1)
    assert packet["cmd"] == "asd_audio"
    assert packet["stream_id"] == start["stream_id"]
    assert len(base64.b64decode(packet["pcm16_b64"], validate=True)) == 2560
    assert isinstance(packet["seq"], int) and packet["seq"] >= 0
    assert face["event"] == "faces"
    asd = face["tracks"][0]["asd"]
    assert asd["stream_id"] == start["stream_id"]
    assert asd["frame_secs"] == 0.04
    assert len(asd["probabilities"]) == 25
    assert all(
        isinstance(p, (int, float)) and 0 <= p <= 1 for p in asd["probabilities"]
    )


def test_shared_asd_protocol_v2():
    data = json.loads(
        (Path(__file__).parent / "fixtures/asd_protocol_v2.json").read_text()
    )
    start, track = data["start"], data["track"]
    status, asd = data["track_status"], data["asd"]
    assert (
        start["cmd"],
        start["version"],
        start["sample_rate"],
        start["channels"],
    ) == ("asd_audio_start", 2, 16000, 1)
    assert track == {
        "cmd": "asd_track",
        "stream_id": start["stream_id"],
        "track_id": 1,
    }
    assert status["event"] == "asd_track_status" and status["status"] == "pinned"
    assert (status["stream_id"], status["track_id"]) == (start["stream_id"], 1)
    assert asd["event"] == "asd" and asd["stream_id"] == start["stream_id"]
    assert asd["frame_secs"] == 0.04 and len(asd["probabilities"]) == 25
    assert all(
        isinstance(p, (int, float)) and 0 <= p <= 1 for p in asd["probabilities"]
    )
