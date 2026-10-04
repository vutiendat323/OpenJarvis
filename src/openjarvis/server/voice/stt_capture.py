"""Opt-in local recording of what the kiosk STT hears, for accuracy review.

Set ``OPENJARVIS_STT_CAPTURE_DIR`` to record each voice session under it:

* ``raw.wav``    the microphone as the pipeline received it
* ``heard.wav``  the audio actually sent to Gemini (after the speaker gate,
                 delay and separation; identical to ``raw.wav`` when unmasked)
* ``transcripts.jsonl``  each final transcript with how many seconds of each
                 stream existed when it arrived, to find the audio to listen to

The recordings are customers' voices: they stay on this machine, are readable
only by the current user, and are never written unless the variable is set.
"""

from __future__ import annotations

import json
import os
import struct
import time
from datetime import datetime
from pathlib import Path

CAPTURE_DIR_ENV = "OPENJARVIS_STT_CAPTURE_DIR"
_HEADER = struct.Struct("<4sI4s4sIHHIIHH4sI")
# Rewrite the WAV sizes about once per second of audio, so a killed process
# leaves a file that still opens.
_PATCH_EVERY_BYTES = 32_000


class _WavStream:
    """Mono PCM16 WAV whose header is kept valid as audio is appended."""

    def __init__(self, path: Path, sample_rate: int) -> None:
        self._rate = sample_rate
        self._file = os.fdopen(
            os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb"
        )
        self.bytes = 0
        self._patched = 0
        self._patch()

    @property
    def seconds(self) -> float:
        return self.bytes / (self._rate * 2)

    def write(self, audio: bytes) -> None:
        self._file.write(audio)
        self.bytes += len(audio)
        if self.bytes - self._patched >= _PATCH_EVERY_BYTES:
            self._patch()

    def _patch(self) -> None:
        self._file.seek(0)
        self._file.write(
            _HEADER.pack(
                b"RIFF",
                36 + self.bytes,
                b"WAVE",
                b"fmt ",
                16,
                1,
                1,
                self._rate,
                self._rate * 2,
                2,
                16,
                b"data",
                self.bytes,
            )
        )
        self._file.seek(0, os.SEEK_END)
        self._file.flush()
        self._patched = self.bytes

    def close(self) -> None:
        self._patch()
        self._file.close()


class SttCapture:
    def __init__(self, directory: Path) -> None:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        self._dir = Path(directory).expanduser() / stamp
        self._dir.mkdir(parents=True, mode=0o700, exist_ok=True)
        self._dir.chmod(0o700)
        self._streams: dict[str, _WavStream] = {}
        self._transcripts = os.fdopen(
            os.open(
                self._dir / "transcripts.jsonl",
                os.O_WRONLY | os.O_CREAT | os.O_APPEND,
                0o600,
            ),
            "a",
            encoding="utf-8",
        )

    def _write(self, name: str, audio: bytes, sample_rate: int) -> None:
        stream = self._streams.get(name)
        if stream is None:
            stream = self._streams[name] = _WavStream(
                self._dir / f"{name}.wav", sample_rate
            )
        stream.write(audio)

    def raw(self, audio: bytes, sample_rate: int) -> None:
        self._write("raw", audio, sample_rate)

    def heard(self, audio: bytes, sample_rate: int) -> None:
        self._write("heard", audio, sample_rate)

    def transcript(self, text: str) -> None:
        seconds = {
            f"{name}_s": round(self._streams[name].seconds, 3)
            if name in self._streams
            else 0.0
            for name in ("raw", "heard")
        }
        line = {"at": time.time(), "text": text, **seconds}
        self._transcripts.write(json.dumps(line, ensure_ascii=False) + "\n")
        self._transcripts.flush()

    def close(self) -> None:
        for stream in self._streams.values():
            stream.close()
        self._transcripts.close()


def capture_from_environment() -> SttCapture | None:
    directory = os.environ.get(CAPTURE_DIR_ENV, "").strip()
    return SttCapture(Path(directory)) if directory else None


__all__ = ["CAPTURE_DIR_ENV", "SttCapture", "capture_from_environment"]
