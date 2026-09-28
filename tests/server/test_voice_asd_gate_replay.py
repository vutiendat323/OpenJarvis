import copy

from scripts.voice_asd_gate_replay import replay


def evidence():
    import json
    from pathlib import Path

    path = Path(__file__).parents[1] / "fixtures/asd_gate_evidence.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_real_gates_counts_and_read_only():
    rows = evidence()
    original = copy.deepcopy(rows)
    report = replay(rows)
    assert report["mar"]["false_accept"] == 1
    assert report["asd_mar"]["false_accept"] == 0
    assert report["mar"]["customer_accept"] == 0
    assert report["asd_mar"]["customer_accept"] == 1
    assert rows == original


def test_late_result_cannot_improve_verdict():
    rows = evidence()
    rows[3]["received_at"] = 12.1
    report = replay(rows)
    assert report["asd_mar"]["customer_accept"] == report["mar"]["customer_accept"]


def test_missing_evidence_unavailable():
    assert replay([])["available"] is False
    rows = evidence()
    del rows[1]["event"]["tracks"][0]["mouth_activity"]
    assert replay(rows)["available"] is False
    assert replay(evidence()[1:])["available"] is False
