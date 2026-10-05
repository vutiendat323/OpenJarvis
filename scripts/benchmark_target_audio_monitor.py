"""Replay final PCM through real Gemini adapter; no cloud/model requests.

Compare OFF / ON / a blocked OS pipe / missing output. Only the external Gemini
session and player process are substituted. The optional hardware smoke uses
paplay with silence on the backend machine's local default output.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import platform
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path
from unittest.mock import patch

from loguru import logger

from openjarvis.server.voice import target_audio_monitor as playback
from openjarvis.server.voice.transcription import (
    GeminiSTTProfile,
    OpenJarvisGeminiSTTService,
)

PCM = (b"\x34\x12" * 320, bytes(640), b"\xff\x3f" * 320)
CASES = ("off", "on", "stalled", "missing_device")


def stats(values):
    ordered = sorted(values)
    return {
        "n": len(ordered),
        **{
            f"p{percentile}": round(
                ordered[math.ceil(len(ordered) * percentile / 100) - 1], 3
            )
            if ordered
            else None
            for percentile in (50, 95, 99)
        },
    }


def player(case, processes):
    def start(rate):
        if case == "missing_device":
            raise OSError("no local audio device (benchmark)")
        script = (
            "import os\nwhile os.read(0, 640): pass\n"
            if case == "on"
            else "import time; time.sleep(60)"
        )
        process = subprocess.Popen(
            [sys.executable, "-u", "-c", script],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            bufsize=0,
        )
        processes.append(process)
        return process

    return start


async def measure(case, frames, interval):
    service = OpenJarvisGeminiSTTService(
        profile=GeminiSTTProfile(),
        api_key="benchmark-no-network",
    )
    service._sample_rate = 16000
    monitor = service.target_audio_monitor
    enqueue_us, send_us, loop_lag_us = [], [], []
    enabled_send_us, recovery_send_us = [], []
    processes, expected_audio = [], []
    delivered = hashlib.sha256()
    expected = hashlib.sha256()
    sent_frames = 0
    frame_started = 0
    frame_monitor_enabled = False
    stop = asyncio.Event()

    class Session:
        async def send_realtime_input(self, *, audio=None, audio_stream_end=False):
            nonlocal sent_frames
            if audio is not None:
                elapsed = (time.perf_counter_ns() - frame_started) / 1000
                send_us.append(elapsed)
                if frame_monitor_enabled:
                    enabled_send_us.append(elapsed)
                elif case != "off":
                    recovery_send_us.append(elapsed)
                assert audio.data == expected_audio[-1]
                delivered.update(audio.data)
                sent_frames += 1

    service._session = Session()
    offer = monitor.offer

    def timed_offer(audio, rate):
        started = time.perf_counter_ns()
        offer(audio, rate)
        enqueue_us.append((time.perf_counter_ns() - started) / 1000)

    monitor.offer = timed_offer

    async def heartbeat():
        loop = asyncio.get_running_loop()
        while not stop.is_set():
            deadline = loop.time() + 0.005
            await asyncio.sleep(0.005)
            loop_lag_us.append(max(0, loop.time() - deadline) * 1_000_000)

    beat = asyncio.create_task(heartbeat())
    try:
        with patch.object(playback, "_open_player", player(case, processes)):
            if case != "off":
                await asyncio.to_thread(monitor.set_enabled, True)
            loop = asyncio.get_running_loop()
            next_frame = loop.time()
            for i in range(frames):
                audio = PCM[i % len(PCM)]
                expected_audio.append(audio)
                expected.update(audio)
                frame_monitor_enabled = monitor.enabled
                frame_started = time.perf_counter_ns()
                async for _ in service.run_stt(audio):
                    pass
                next_frame += interval
                await asyncio.sleep(max(0, next_frame - loop.time()))
            snapshot = monitor.snapshot()
            await service._send_finalization_signal()
            assert sent_frames == frames and delivered.digest() == expected.digest()
            await service.cleanup()
        assert all(process.poll() is not None for process in processes)
    finally:
        stop.set()
        await beat
        await asyncio.to_thread(monitor.close)
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=2)
    return {
        "enqueue_us": enqueue_us,
        "audio_to_stt_us": send_us,
        "event_loop_lag_us": loop_lag_us,
        "enabled_audio_to_stt_us": enabled_send_us,
        "recovery_audio_to_stt_us": recovery_send_us,
        "snapshot": snapshot,
        "stt_frames": sent_frames,
        "stt_sha256": delivered.hexdigest(),
    }


async def hardware_smoke():
    service = OpenJarvisGeminiSTTService(
        profile=GeminiSTTProfile(),
        api_key="benchmark-no-network",
    )
    service._sample_rate = 16000
    monitor = service.target_audio_monitor
    sent = 0

    class Session:
        async def send_realtime_input(self, *, audio):
            nonlocal sent
            assert audio.data == bytes(640)
            sent += 1

    service._session = Session()
    await asyncio.to_thread(monitor.set_enabled, True)
    try:
        for _ in range(25):
            async for _ in service.run_stt(bytes(640)):
                pass
            await asyncio.sleep(0.02)
        result = monitor.snapshot()
        result["stt_frames_sent"] = sent
        assert sent == 25
        result["pcm"] = "silence; tests device acceptance, not acoustic output"
        return result
    finally:
        await service.cleanup()


async def main(args):
    logger.remove()  # No transcript logs or provider credentials in artifacts.
    samples = defaultdict(lambda: defaultdict(list))
    runs = []
    for repetition in range(args.repetitions):
        # Rotate case order so OFF is not always the cold-start sample.
        order = CASES[repetition % 4 :] + CASES[: repetition % 4]
        for case in order:
            result = await measure(case, args.frames, args.interval)
            for metric in (
                "enqueue_us",
                "audio_to_stt_us",
                "event_loop_lag_us",
                "enabled_audio_to_stt_us",
                "recovery_audio_to_stt_us",
            ):
                samples[case][metric].extend(result[metric])
            runs.append({"case": case, "repetition": repetition, **result})
            print(
                f"{case}: repetition {repetition + 1}, "
                f"{result['stt_frames']} STT frames",
                flush=True,
            )
    summary = {
        case: {metric: stats(values) for metric, values in metrics.items()}
        for case, metrics in samples.items()
    }
    off_p99 = summary["off"]["audio_to_stt_us"]["p99"]
    checks = {}
    for case in CASES[1:]:
        overhead = summary[case]["audio_to_stt_us"]["p99"] - off_p99
        summary[case]["audio_to_stt_p99_delta_us"] = round(overhead, 3)
        enabled_p99 = summary[case]["enabled_audio_to_stt_us"]["p99"]
        summary[case]["enabled_audio_to_stt_p99_delta_us"] = (
            round(enabled_p99 - off_p99, 3) if enabled_p99 is not None else None
        )
        checks[f"{case}_enqueue_p99_below_100us"] = (
            summary[case]["enqueue_us"]["p99"] is not None
            and summary[case]["enqueue_us"]["p99"] < 100
        )
        checks[f"{case}_stt_p99_delta_below_1000us"] = overhead < 1000
        checks[f"{case}_enabled_stt_p99_delta_below_1000us"] = (
            enabled_p99 is not None and enabled_p99 - off_p99 < 1000
        )
    root = Path(__file__).resolve().parents[1]
    source_files = (
        "target_audio_monitor.py",
        "transcription.py",
        "speaker_console.py",
        "speaker_vision.py",
        "speaker_audio.py",
        "pipeline.py",
    )
    report = {
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
        },
        "parameters": {
            "frames_per_case_per_run": args.frames,
            "repetitions": args.repetitions,
            "interval_secs": args.interval,
            "pcm": "int16 mono 16kHz, 20ms frames; target/silence/TSE fixture",
        },
        "scope": (
            "Local audio-to-STT send and scheduling only; no live Gemini/Agent latency"
        ),
        "summary": summary,
        "checks": checks,
        "runs": runs,
        "source_sha256": {
            file: hashlib.sha256(
                (root / "src/openjarvis/server/voice" / file).read_bytes()
            ).hexdigest()
            for file in source_files
        },
    }
    if args.hardware_smoke:
        report["hardware_smoke"] = await hardware_smoke()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {
                "summary": summary,
                "checks": checks,
                "hardware_smoke": report.get("hardware_smoke"),
            },
            indent=2,
        )
    )
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frames", type=int, default=500)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--interval", type=float, default=0.02)
    parser.add_argument("--hardware-smoke", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.frames < 100 or args.repetitions < 1 or args.interval <= 0:
        parser.error("use >=100 frames, >=1 repetition and a positive interval")
    # Respect capture opt-in in serving; benchmark must never write user recordings.
    with patch.dict(os.environ, {"OPENJARVIS_STT_CAPTURE_DIR": ""}):
        sys.exit(asyncio.run(main(args)))
