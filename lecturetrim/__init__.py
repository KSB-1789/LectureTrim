"""LectureTrim core modules."""

from .media import (
    DependencyError,
    MediaError,
    VideoInfo,
    detect_nvenc_support,
    export_trimmed_video,
    extract_audio_16k_mono,
    probe_video,
)
from .timeline import (
    Interval,
    Segment,
    TimelineResult,
    build_timeline,
    format_duration,
)
from .vad import VadConfig, detect_speech_intervals

__all__ = [
    "DependencyError",
    "Interval",
    "MediaError",
    "Segment",
    "TimelineResult",
    "VadConfig",
    "VideoInfo",
    "build_timeline",
    "detect_nvenc_support",
    "detect_speech_intervals",
    "export_trimmed_video",
    "extract_audio_16k_mono",
    "format_duration",
    "probe_video",
]
