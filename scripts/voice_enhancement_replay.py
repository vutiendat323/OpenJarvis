"""Replay saved 16-kHz mono PCM16 WAV through the optional microphone filter."""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
import wave
from array import array
from pathlib import Path

from openjarvis.server.voice.speaker import SpeakerSettings
from openjarvis.server.voice.speaker_enhancement import build_audio_enhancer


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    return ordered[lower] + (
        ordered[min(lower + 1, len(ordered) - 1)] - ordered[lower]
    ) * (position - lower)


async def replay(input_path: Path, output_path: Path, *, chunk_ms: int = 20) -> dict:
    """Process a saved WAV without masking the filter's startup or tail buffering."""
    input_path = Path(input_path)
    output_path = Path(output_path)
    if input_path.resolve() == output_path.resolve():
        raise ValueError("input and output must be different files")
    if chunk_ms < 1 or 16000 * chunk_ms % 1000:
        raise ValueError("chunk_ms must be a positive whole-sample duration")

    with wave.open(str(input_path), "rb") as source:
        if source.getframerate() != 16000:
            raise ValueError("input must use 16000 Hz")
        if source.getnchannels() != 1:
            raise ValueError("input must be mono")
        if source.getsampwidth() != 2 or source.getcomptype() != "NONE":
            raise ValueError("input must be uncompressed PCM16")
        input_samples = source.getnframes()
        started = time.perf_counter()
        enhancer = build_audio_enhancer(
            SpeakerSettings(enabled=True, enhancer="rnnoise")
        )
        effective = "none"
        if enhancer is not None:
            await enhancer.start(16000)
            effective = enhancer.effective_name
        processing_ms: list[float] = []
        first_output_ms = None
        output_samples = 0
        peak = 0
        try:
            with wave.open(str(output_path), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(16000)
                while pcm := source.readframes(16 * chunk_ms):
                    step_started = time.perf_counter()
                    filtered = (
                        await enhancer.filter(pcm) if enhancer is not None else pcm
                    )
                    processing_ms.append((time.perf_counter() - step_started) * 1000)
                    if len(filtered) % 2:
                        raise ValueError("filter returned incomplete PCM16 sample")
                    effective = (
                        enhancer.effective_name if enhancer is not None else "none"
                    )
                    if filtered:
                        if first_output_ms is None:
                            first_output_ms = (time.perf_counter() - started) * 1000
                        samples = array("h")
                        samples.frombytes(filtered)
                        peak = max(peak, max(abs(value) for value in samples))
                        output.writeframes(filtered)
                        output_samples += len(samples)
        finally:
            if enhancer is not None:
                await enhancer.stop()

    warm_ms = processing_ms[1:] or processing_ms
    return {
        "requested": "rnnoise",
        "effective": effective,
        "sample_rate": 16000,
        "input_samples": input_samples,
        "output_samples": output_samples,
        "sample_delta": output_samples - input_samples,
        "first_output_ms": first_output_ms,
        "processing_p50_ms": statistics.median(warm_ms) if warm_ms else None,
        "processing_p95_ms": _percentile(warm_ms, 0.95) if warm_ms else None,
        "rtf": sum(processing_ms) / (input_samples / 16) if input_samples else None,
        "peak_abs": peak / 32768,
        "nonfinite_samples": 0,
        "chunk_ms": chunk_ms,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--chunk-ms", type=int, default=20)
    args = parser.parse_args()
    report = asyncio.run(replay(args.input, args.output, chunk_ms=args.chunk_ms))
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
