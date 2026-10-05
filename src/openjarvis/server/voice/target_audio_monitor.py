"""Best-effort local playback of the final Gemini PCM, never browser audio.

OFF creates no thread/device. The producer only offers immutable bytes under a
nonblocking queue lock. All process/device I/O and waits belong to the worker.
"""

from __future__ import annotations

import logging
import os
import select
import shutil
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)
BUFFER_SECS = 0.2
WRITE_TIMEOUT_SECS = 0.2


def _open_player(rate: int) -> subprocess.Popen:
    executable = shutil.which("paplay")
    if executable is None:
        raise FileNotFoundError("paplay is not installed")
    runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    server = f"--server=unix:{runtime}/pulse/native"
    control = shutil.which("pactl")
    if control is None:
        raise FileNotFoundError("pactl is not installed")
    sink = subprocess.check_output(
        [control, server, "get-default-sink"],
        text=True,
        stderr=subprocess.DEVNULL,
        timeout=0.5,
    ).strip()
    if not sink or sink == "auto_null":
        raise OSError(
            "No hardware audio output is selected (default sink is auto_null)"
        )
    # Pin to a local Unix socket, even when PULSE_SERVER names a remote server.
    return subprocess.Popen(
        [
            executable,
            server,
            f"--device={sink}",
            "--raw",
            "--format=s16le",
            "--channels=1",
            f"--rate={rate}",
            "--latency-msec=40",
            "--stream-name=OpenJarvis target audio",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        bufsize=0,
    )


@dataclass
class _Playback:
    pending: deque[tuple[float, bytes]] = field(default_factory=deque)
    lock: threading.Lock = field(default_factory=threading.Lock)
    stop: threading.Event = field(default_factory=threading.Event)
    rate: int = 0
    queued_bytes: int = 0
    process: subprocess.Popen | None = None
    thread: threading.Thread | None = None


class LocalTargetAudioMonitor:
    def __init__(self) -> None:
        self._run: _Playback | None = None
        self._control_lock = threading.Lock()
        self._closed = False
        self._status = "off"
        self._error: str | None = None
        self._dropped = 0

    @property
    def enabled(self) -> bool:
        run = self._run
        return run is not None and not run.stop.is_set()

    def snapshot(self) -> dict:
        run = self._run
        return {
            "enabled": self.enabled,
            "status": self._status,
            "error": self._error,
            "dropped_frames": self._dropped,
            "queued_ms": run.queued_bytes * 500 / run.rate if run and run.rate else 0,
        }

    def offer(self, audio: bytes, sample_rate: int) -> None:
        """No I/O, PCM conversion, blocking lock, or wait on the STT path."""
        run = self._run
        if run is None or run.stop.is_set():
            return
        if not audio or len(audio) % 2 or sample_rate <= 0:
            self._dropped += 1
            return
        if not run.lock.acquire(blocking=False):
            self._dropped += 1
            return
        try:
            if run.stop.is_set():
                return
            if run.rate and run.rate != sample_rate:
                self.fail("Audio rate changed during local playback")
                return
            run.rate = sample_rate
            limit = int(sample_rate * 2 * BUFFER_SECS)
            if len(audio) > limit:
                self._dropped += 1
                return
            while run.pending and (
                run.queued_bytes + len(audio) > limit or len(run.pending) >= 32
            ):
                run.queued_bytes -= len(run.pending.popleft()[1])
                self._dropped += 1
            run.pending.append((time.monotonic(), audio))
            run.queued_bytes += len(audio)
        finally:
            run.lock.release()

    def fail(self, error: str) -> None:
        """Disable offers without touching a device or waiting for the worker."""
        self._error = error[:200]
        self._status = "error"
        if self._run is not None:
            self._run.stop.set()

    def set_enabled(self, enabled: bool) -> None:
        """Control path only; callers run this off the Voice event loop."""
        if type(enabled) is not bool:
            raise ValueError("Target audio monitor requires ON or OFF")
        with self._control_lock:
            if enabled and self.enabled:
                return
            self._shutdown()
            if enabled:
                if self._closed:
                    raise ValueError("Voice session has ended")
                run = _Playback()
                self._run = run
                self._status, self._error = "waiting", None
                run.thread = threading.Thread(
                    target=self._play,
                    args=(run,),
                    name="target_audio_monitor",
                    daemon=True,
                )
                run.thread.start()

    def _shutdown(self) -> None:
        run, self._run = self._run, None
        if run is not None:
            run.stop.set()
            self._kill(run.process)
            if run.thread is not None:
                run.thread.join(timeout=0.5)
        self._status, self._error = "off", None

    def close(self) -> None:
        with self._control_lock:
            self._closed = True
            self._shutdown()

    @staticmethod
    def _kill(process: subprocess.Popen | None) -> None:
        if process is not None and process.poll() is None:
            try:
                process.kill()
            except OSError:
                pass

    def _play(self, run: _Playback) -> None:
        try:
            while not run.stop.is_set():
                if run.process is not None and run.process.poll() is not None:
                    raise OSError(
                        f"Local audio player exited ({run.process.returncode})"
                    )
                with run.lock:
                    packet = run.pending.popleft() if run.pending else None
                    if packet is not None:
                        run.queued_bytes -= len(packet[1])
                if packet is None:
                    run.stop.wait(0.01)
                    continue
                offered_at, audio = packet
                if time.monotonic() - offered_at > BUFFER_SECS:
                    self._dropped += 1
                    continue
                if run.process is None:
                    run.process = _open_player(run.rate)
                    os.set_blocking(run.process.stdin.fileno(), False)
                    # Bound the pipe as well as the Python queue (128 ms at 16 kHz).
                    import fcntl

                    fcntl.fcntl(run.process.stdin, fcntl.F_SETPIPE_SZ, 4096)
                if time.monotonic() - offered_at > BUFFER_SECS:
                    self._dropped += 1
                    continue
                view = memoryview(audio)
                deadline = time.monotonic() + WRITE_TIMEOUT_SECS
                fd = run.process.stdin.fileno()
                while view and not run.stop.is_set():
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Local audio playback stalled")
                    if not select.select([], [fd], [], 0.01)[1]:
                        continue
                    try:
                        view = view[os.write(fd, view) :]
                    except BlockingIOError:
                        continue
                if self._run is run and not run.stop.is_set():
                    self._status = "playing"
        except Exception as exc:  # Optional output must never stop Voice.
            if self._run is run and not run.stop.is_set():
                self.fail(str(exc))
                logger.warning("Target audio monitor stopped: %s", exc)
        finally:
            self._kill(run.process)
            if run.process is not None:
                run.process.stdin.close()
                try:
                    run.process.wait(timeout=0.5)
                except subprocess.TimeoutExpired:
                    pass
            with run.lock:
                run.pending.clear()
                run.queued_bytes = 0
