"""Replay a WAV through the real diarizer and audio-only speaker gate.

    .venv/bin/python scripts/voice_speaker_replay.py sample.wav --bot 6.69-7.12 ...

Prints the verdict counts and a per-second strip (A accept, R reject,
U uncertain, . silence). Use it to measure the gate on recorded kiosk audio.
"""

from __future__ import annotations

import argparse
import wave
from collections import Counter
from pathlib import Path

import numpy as np

from openjarvis.server.voice.speaker import AudioOnlyGate, SpeakerSettings, Verdict
from openjarvis.server.voice.speaker_audio import (
    BOT_TAIL_SECS,
    SAMPLE_RATE,
    SortformerDiarizer,
)


def replay(
    wav: Path, bot_intervals: list[tuple[float, float]], *, latency: str
) -> list[tuple[float, Verdict | None]]:
    with wave.open(str(wav)) as source:
        if source.getframerate() != SAMPLE_RATE or source.getnchannels() != 1:
            raise SystemExit("need 16 kHz mono WAV")
        audio = np.frombuffer(source.readframes(source.getnframes()), dtype=np.int16)
    diarizer = SortformerDiarizer(latency)
    gate = AudioOnlyGate(SpeakerSettings(enabled=True, diarizer="sortformer"))
    frames: list[tuple[float, Verdict | None]] = []
    step = diarizer.chunk_samples
    for start in range(0, len(audio) - step + 1, step):
        t = start / SAMPLE_RATE
        # Same reverb tail the live processor applies after playback stops.
        bot = any(a <= t < b + BOT_TAIL_SECS for a, b in bot_intervals)
        for row in diarizer.push(audio[start : start + step]):
            frames.append(
                (len(frames) * diarizer.frame_secs, gate.frame(row, bot_speaking=bot))
            )
    return frames


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("wav", type=Path)
    parser.add_argument(
        "--bot", nargs="*", default=[], help="playback intervals START-END (s)"
    )
    parser.add_argument("--latency", default="ultra_low", choices=["ultra_low", "low"])
    args = parser.parse_args()
    bot = [tuple(float(x) for x in span.split("-")) for span in args.bot]
    frames = replay(args.wav, bot, latency=args.latency)
    symbol = {
        Verdict.ACCEPT: "A",
        Verdict.REJECT: "R",
        Verdict.UNCERTAIN: "U",
        None: ".",
    }
    print(Counter(symbol[v] for _, v in frames))
    per_second = int(1 / 0.08)
    strip = "".join(symbol[v] for _, v in frames)
    for i in range(0, len(strip), per_second):
        print(f"{i * 0.08:6.1f}s {strip[i : i + per_second]}")


if __name__ == "__main__":
    main()
