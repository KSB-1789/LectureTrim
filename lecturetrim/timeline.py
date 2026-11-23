from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Literal, Tuple

Interval = Tuple[float, float]


@dataclass(frozen=True)
class Segment:
    index: int
    status: Literal["KEEP", "CUT"]
    start: float
    end: float
    duration: float
    reason: str


@dataclass(frozen=True)
class TimelineResult:
    original_duration: float
    trimmed_duration: float
    time_saved: float
    time_saved_pct: float
    keep_intervals: List[Interval]
    cut_intervals: List[Interval]
    segments: List[Segment]


def format_duration(seconds: float) -> str:
    """Format seconds as HH:MM:SS.d or MM:SS.d."""
    seconds = max(0.0, float(seconds))
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60.0
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:05.2f}"
    return f"{minutes:02d}:{secs:05.2f}"


def normalize_intervals(
    intervals: Iterable[Interval],
    max_duration: float,
    min_length: float = 1e-3,
) -> List[Interval]:
    """Clamp intervals to [0, max_duration], sort, and merge overlaps."""
    cleaned: List[Interval] = []
    for start, end in intervals:
        s = max(0.0, min(float(start), max_duration))
        e = max(0.0, min(float(end), max_duration))
        if e - s > min_length:
            cleaned.append((s, e))

    if not cleaned:
        return []

    cleaned.sort(key=lambda x: (x[0], x[1]))
    merged: List[Interval] = [cleaned[0]]
    for start, end in cleaned[1:]:
        prev_start, prev_end = merged[-1]
        if start <= prev_end:
            merged[-1] = (prev_start, max(prev_end, end))
        else:
            merged.append((start, end))
    return merged


def merge_close_intervals(
    intervals: List[Interval],
    min_gap_sec: float,
) -> List[Interval]:
    """Merge adjacent KEEP intervals separated by less than min_gap_sec."""
    if not intervals:
        return []
    merged: List[Interval] = [intervals[0]]
    for start, end in intervals[1:]:
        prev_start, prev_end = merged[-1]
        if (start - prev_end) < min_gap_sec:
            merged[-1] = (prev_start, max(prev_end, end))
        else:
            merged.append((start, end))
    return merged


def _intervals_overlap(a: Interval, b: Interval) -> bool:
    return min(a[1], b[1]) - max(a[0], b[0]) > 1e-3


def _classify_keep_reason(
    seg: Interval,
    speech_intervals: List[Interval],
    visual_intervals: List[Interval],
) -> str:
    has_speech = any(_intervals_overlap(seg, sp) for sp in speech_intervals)
    has_visual = any(_intervals_overlap(seg, vi) for vi in visual_intervals)
    if has_speech and has_visual:
        return "Speech + Visual"
    if has_speech:
        return "Speech"
    if has_visual:
        return "Visual Activity"
    return "Gap Merged"


def build_timeline(
    speech_intervals: Iterable[Interval],
    visual_intervals: Iterable[Interval],
    total_duration: float,
    min_gap_to_cut_sec: float = 6.0,
) -> TimelineResult:
    """Compute union of speech and visual intervals, merge short gaps, and build segments."""
    total_duration = max(0.0, float(total_duration))
    norm_speech = normalize_intervals(speech_intervals, total_duration)
    norm_visual = normalize_intervals(visual_intervals, total_duration)

    union_intervals = normalize_intervals(
        [*norm_speech, *norm_visual],
        max_duration=total_duration,
    )
    keep_intervals = merge_close_intervals(union_intervals, min_gap_to_cut_sec)

    segments: List[Segment] = []
    cut_intervals: List[Interval] = []
    cursor = 0.0
    idx = 1

    for start, end in keep_intervals:
        if start - cursor > 1e-3:
            cut_intervals.append((cursor, start))
            segments.append(
                Segment(
                    index=idx,
                    status="CUT",
                    start=cursor,
                    end=start,
                    duration=start - cursor,
                    reason="Idle / Dead Time",
                )
            )
            idx += 1

        segments.append(
            Segment(
                index=idx,
                status="KEEP",
                start=start,
                end=end,
                duration=end - start,
                reason=_classify_keep_reason((start, end), norm_speech, norm_visual),
            )
        )
        idx += 1
        cursor = end

    if total_duration - cursor > 1e-3:
        cut_intervals.append((cursor, total_duration))
        segments.append(
            Segment(
                index=idx,
                status="CUT",
                start=cursor,
                end=total_duration,
                duration=total_duration - cursor,
                reason="Idle / Dead Time",
            )
        )

    trimmed_duration = sum(end - start for start, end in keep_intervals)
    time_saved = max(0.0, total_duration - trimmed_duration)
    time_saved_pct = (time_saved / total_duration * 100.0) if total_duration > 0 else 0.0

    return TimelineResult(
        original_duration=total_duration,
        trimmed_duration=trimmed_duration,
        time_saved=time_saved,
        time_saved_pct=time_saved_pct,
        keep_intervals=keep_intervals,
        cut_intervals=cut_intervals,
        segments=segments,
    )
