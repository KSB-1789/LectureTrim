from __future__ import annotations

import wave
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple

import numpy as np

from .timeline import Interval, normalize_intervals


@dataclass(frozen=True)
class VadConfig:
    # Conservative defaults: keep quiet speech and bridge short pauses rather
    # than chopping the teacher's sentences into fragments.
    threshold: float = 0.35
    pre_pad_sec: float = 0.6
    post_pad_sec: float = 0.6
    min_speech_duration_ms: int = 250
    min_silence_duration_ms: int = 300


_SILERO_CACHE = {}


def _get_torch():
    """Import torch only when analysis starts, so the app window opens fast."""
    try:
        import torch
    except ImportError as exc:  # pragma: no cover - packaging guard
        raise RuntimeError(
            "PyTorch is required for speech detection. Reinstall the app or run "
            "`pip install torch` in a source checkout."
        ) from exc
    return torch


def _load_silero_model():
    """Load and cache Silero VAD model + timestamp utility."""
    if "model" in _SILERO_CACHE:
        return _SILERO_CACHE["model"], _SILERO_CACHE["get_speech_timestamps"]

    torch = _get_torch()
    try:
        from silero_vad import get_speech_timestamps, load_silero_vad

        model = load_silero_vad()
    except ImportError:
        model, utils = torch.hub.load(
            repo_or_dir="snakers4/silero-vad",
            model="silero_vad",
            force_reload=False,
            trust_repo=True,
        )
        get_speech_timestamps = utils[0]

    _SILERO_CACHE["model"] = model
    _SILERO_CACHE["get_speech_timestamps"] = get_speech_timestamps
    return model, get_speech_timestamps


def _read_wav_16k_mono(wav_path: Path):
    """Read a 16 kHz mono 16-bit PCM WAV file into a float32 tensor in [-1, 1]."""
    torch = _get_torch()
    with wave.open(str(wav_path), "rb") as wf:
        if wf.getnchannels() != 1 or wf.getframerate() != 16000 or wf.getsampwidth() != 2:
            raise ValueError(
                f"Expected 16 kHz mono 16-bit PCM WAV, got "
                f"{wf.getframerate()} Hz, {wf.getnchannels()} ch, {wf.getsampwidth()*8}-bit."
            )
        raw = wf.readframes(wf.getnframes())

    if not raw:
        return torch.zeros(0, dtype=torch.float32)

    audio_np = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    return torch.from_numpy(audio_np)


def detect_speech_intervals(
    wav_path: Path,
    total_duration: float,
    config: VadConfig,
) -> List[Interval]:
    """Detect padded speech intervals using Silero VAD."""
    audio = _read_wav_16k_mono(wav_path)
    if audio.numel() == 0:
        return []

    model, get_speech_timestamps = _load_silero_model()

    raw_segments = get_speech_timestamps(
        audio,
        model,
        threshold=config.threshold,
        sampling_rate=16000,
        min_speech_duration_ms=config.min_speech_duration_ms,
        min_silence_duration_ms=config.min_silence_duration_ms,
    )

    padded: List[Tuple[float, float]] = []
    for seg in raw_segments:
        start_sec = seg["start"] / 16000.0 - config.pre_pad_sec
        end_sec = seg["end"] / 16000.0 + config.post_pad_sec
        padded.append((start_sec, end_sec))

    return normalize_intervals(padded, max_duration=total_duration)
