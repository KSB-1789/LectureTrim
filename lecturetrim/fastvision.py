"""Cheap visual-activity detector used to keep board writing without audio.

Runs one FFmpeg process that outputs 1 fps, 320x180 grayscale frames. Compared
with the legacy 2 fps 1080p pipeline this is roughly two orders of magnitude
cheaper, which matters for two-hour lectures.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable, List, Tuple

import numpy as np

from .media import MediaError, _find_ffmpeg_binaries
from .timeline import Interval, merge_close_intervals, normalize_intervals

WIDTH = 320
HEIGHT = 180
FRAME_BYTES = WIDTH * HEIGHT


def _read_exact(stream, size: int) -> bytes | None:
    chunks: List[bytes] = []
    remaining = size
    while remaining > 0:
        chunk = stream.read(remaining)
        if not chunk:
            return None if not chunks else b"".join(chunks)
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def detect_writing_intervals(
    video_path: Path,
    total_duration: float,
    progress_callback: Callable[[float, str], None] | None = None,
) -> List[Interval]:
    ffmpeg_bin, _ = _find_ffmpeg_binaries()
    cmd = [
        ffmpeg_bin,
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(video_path),
        "-vf",
        f"fps=1,scale={WIDTH}:{HEIGHT}",
        "-pix_fmt",
        "gray",
        "-f",
        "rawvideo",
        "pipe:1",
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    assert proc.stdout is not None

    previous: np.ndarray | None = None
    active: List[bool] = []
    index = 0
    try:
        while True:
            raw = _read_exact(proc.stdout, FRAME_BYTES)
            if raw is None or len(raw) < FRAME_BYTES:
                break
            current = np.frombuffer(raw, dtype=np.uint8)
            if previous is not None:
                diff = np.abs(current.astype(np.int16) - previous.astype(np.int16))
                changed = float(np.mean(diff > 12))
                active.append(changed >= 0.006)
            previous = current
            index += 1
            if progress_callback and index % 300 == 0:
                fraction = min(0.95, index / max(1.0, total_duration))
                progress_callback(fraction, "Checking for board writing…")
    finally:
        if proc.poll() is None:
            proc.terminate()
        proc.wait()

    if proc.returncode != 0 and index == 0:
        raise MediaError("FFmpeg could not sample frames for visual activity.")

    step = 1.0
    padding = 1.5
    raw_intervals: List[Interval] = []
    for i, is_active in enumerate(active):
        if not is_active:
            continue
        t = i * step
        raw_intervals.append((max(0.0, t - step - padding), min(total_duration, t + padding)))

    if not raw_intervals:
        return []

    merged = normalize_intervals(raw_intervals, max_duration=total_duration)
    # A teacher writing for 10 seconds without speaking must stay in the cut.
    return merge_close_intervals(merged, min_gap_sec=12.0)
