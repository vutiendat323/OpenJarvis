"""Replay complete session face/stream history and labeled diarizer decisions.

The initial stream record (including null for disabled) must precede decisions.
All times share one clock. Only receipt times <= a decision deadline are visible.
This is a policy replay, not a model or live latency benchmark.
"""

from __future__ import annotations

import argparse
import copy
import json
import math
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from openjarvis.server.voice.speaker import (
    AudioOnlyGate,
    FaceTrackBuffer,
    SpeakerSettings,
    Verdict,
)


def replay(records):
    def unavailable(reason):
        return {"available": False, "reason": reason}

    history = []
    decisions = []
    try:
        for row in records:
            kind = row["kind"]
            if kind in ("faces", "stream"):
                if not math.isfinite(row["received_at"]):
                    raise ValueError("nonfinite receipt time")
                if kind == "faces":
                    event = row["event"]
                    if event["event"] != "faces" or not math.isfinite(event["ts"]):
                        raise ValueError("invalid face event")
                    for face in event["tracks"]:
                        if (
                            "mouth_activity" not in face
                            or "distance_m" not in face
                            or "track_id" not in face
                        ):
                            raise ValueError("missing MAR/face evidence")
                elif row["stream_id"] is not None and not isinstance(
                    row["stream_id"], str
                ):
                    raise ValueError("invalid stream ID")
                history.append(row)
            elif kind == "speaker":
                if (
                    not all(math.isfinite(row[k]) for k in ("t", "decided_at"))
                    or row["decided_at"] < row["t"]
                ):
                    raise ValueError("invalid decision time")
                if not isinstance(row["customer_speaking"], bool) or not isinstance(
                    row["bot_speaking"], bool
                ):
                    raise ValueError("missing boolean ground truth")
                if not row["probs"] or any(
                    not math.isfinite(p) or not 0 <= p <= 1 for p in row["probs"]
                ):
                    raise ValueError("missing diarizer probabilities")
                row["stream_id"]
                decisions.append(row)
            else:
                raise ValueError("unknown record kind")
    except (KeyError, TypeError, ValueError) as exc:
        return unavailable(str(exc))
    if not decisions or not any(r["kind"] == "faces" for r in history):
        return unavailable("missing labeled diarizer or face/MAR history")
    history.sort(key=lambda r: r["received_at"])
    decisions.sort(key=lambda r: r["decided_at"])
    if not any(
        r["kind"] == "stream" and r["received_at"] <= decisions[0]["decided_at"]
        for r in history
    ):
        return unavailable("missing initial stream history")
    report = {"available": True, "decisions": []}
    faces = [FaceTrackBuffer(), FaceTrackBuffer()]
    gates = [
        AudioOnlyGate(SpeakerSettings(vision_asd=enabled), buffer)
        for enabled, buffer in zip((False, True), faces)
    ]
    totals = [
        dict(
            false_accept=0,
            customer_accept=0,
            customer_frames=0,
            noncustomer_frames=0,
            asd_used=0,
            eligible_anchor_frames=0,
        )
        for _ in gates
    ]
    cursor = 0
    for row in decisions:
        while (
            cursor < len(history)
            and history[cursor]["received_at"] <= row["decided_at"]
        ):
            event = history[cursor]
            for buffer in faces:
                if event["kind"] == "stream":
                    buffer.set_asd_stream(event["stream_id"])
                else:
                    buffer.add(copy.deepcopy(event["event"]))
            cursor += 1
        output = {"t": row["t"], "decided_at": row["decided_at"]}
        for name, gate, buffer, total in zip(("mar", "asd_mar"), gates, faces, totals):
            # The production gate consults wall time for ASD freshness.
            with patch(
                "openjarvis.server.voice.speaker.time.time",
                return_value=row["decided_at"],
            ):
                verdict = gate.frame(
                    row["probs"],
                    bot_speaking=row["bot_speaking"],
                    t=row["t"],
                    asd_stream_id=row["stream_id"],
                )
            customer = row["customer_speaking"]
            total["customer_frames" if customer else "noncustomer_frames"] += 1
            if verdict == Verdict.ACCEPT:
                total["customer_accept" if customer else "false_accept"] += 1
            detail = gate.last_evidence_detail or {}
            total["asd_used"] += detail.get("source") == "asd"
            total["eligible_anchor_frames"] += (
                detail.get("track_id") is not None
                and detail.get("fallback_reason") != "overlap"
            )
            output[name] = {
                "verdict": verdict.value if verdict is not None else None,
                "evidence": detail,
            }
        report["decisions"].append(output)
    for name, total in zip(("mar", "asd_mar"), totals):
        total["customer_accept_recall"] = (
            total["customer_accept"] / total["customer_frames"]
            if total["customer_frames"]
            else None
        )
        total["asd_used_fraction"] = (
            total["asd_used"] / total["eligible_anchor_frames"]
            if total["eligible_anchor_frames"]
            else None
        )
        report[name] = total
    report["live_acceptance"] = (
        "unmeasured: post-warm-up coverage and latency require live evidence"
    )
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    if Path(args.output).resolve() == Path(args.evidence).resolve():
        parser.error("output must not overwrite evidence")
    rows = [
        json.loads(line)
        for line in Path(args.evidence).read_text().splitlines()
        if line.strip()
    ]
    Path(args.output).write_text(json.dumps(replay(rows), indent=2) + "\n")


if __name__ == "__main__":
    main()
