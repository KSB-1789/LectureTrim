from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Callable, Dict, Iterator, List, Optional, Tuple, Union


class DependencyError(RuntimeError):
    """Raised when FFmpeg, FFprobe, or yt-dlp is missing."""


class MediaError(RuntimeError):
    """Raised when media probing, extraction, or encoding fails."""


@dataclass(frozen=True)
class VideoInfo:
    path: Path
    duration: float
    width: int
    height: int
    fps: float
    has_audio: bool


def _bundled_bin_dir() -> Optional[Path]:
    """Directory holding the FFmpeg binaries when running from the packaged app."""
    candidates: List[Optional[str]] = [
        os.environ.get("LECTURETRIM_BIN_DIR"),
        getattr(sys, "_MEIPASS", None),
    ]
    for raw in candidates:
        if not raw:
            continue
        candidate = Path(raw) / "bin"
        if candidate.is_dir():
            return candidate
    return None


def _resolve_binary(name: str) -> Optional[str]:
    """Prefer the packaged binary, then fall back to PATH (source runs)."""
    bundled = _bundled_bin_dir()
    if bundled is not None:
        exe = bundled / (name + (".exe" if os.name == "nt" else ""))
        if exe.is_file():
            return str(exe)
    return shutil.which(name)


def _find_ffmpeg_binaries() -> Tuple[str, str]:
    ffmpeg_bin = _resolve_binary("ffmpeg")
    ffprobe_bin = _resolve_binary("ffprobe")
    if not ffmpeg_bin or not ffprobe_bin:
        raise DependencyError(
            "FFmpeg / FFprobe not found. The packaged app includes them; for a source "
            "run, install FFmpeg and make sure `ffmpeg` and `ffprobe` are on PATH:\n"
            "  • Ubuntu/Debian: sudo apt install ffmpeg\n"
            "  • macOS: brew install ffmpeg\n"
            "  • Windows: winget install Gyan.FFmpeg (then restart your terminal)"
        )
    return ffmpeg_bin, ffprobe_bin






@lru_cache(maxsize=1)
def detect_nvenc_support() -> bool:
    """Verify whether h264_nvenc is compiled AND actually usable on this machine."""
    ffmpeg_bin = _resolve_binary("ffmpeg")
    if not ffmpeg_bin:
        return False

    try:
        enc_proc = subprocess.run(
            [ffmpeg_bin, "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if "h264_nvenc" not in enc_proc.stdout:
            return False

        # Probe actual GPU availability with a 0.1s null encode
        probe_proc = subprocess.run(
            [
                ffmpeg_bin,
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=c=black:s=256x256:d=0.1",
                "-c:v",
                "h264_nvenc",
                "-preset",
                "medium",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        return probe_proc.returncode == 0
    except Exception:
        return False


def _parse_fps(rate_str: Optional[str]) -> float:
    if not rate_str or rate_str == "0/0":
        return 30.0
    if "/" in rate_str:
        num, den = rate_str.split("/", 1)
        try:
            d = float(den)
            return float(num) / d if d > 0 else 30.0
        except ValueError:
            return 30.0
    try:
        return float(rate_str)
    except ValueError:
        return 30.0


def probe_video(video_path: Path) -> VideoInfo:
    """Inspect video metadata with ffprobe."""
    _, ffprobe_bin = _find_ffmpeg_binaries()
    video_path = Path(video_path)
    if not video_path.exists():
        raise MediaError(f"Video file does not exist: {video_path}")

    cmd = [
        ffprobe_bin,
        "-v",
        "error",
        "-show_format",
        "-show_streams",
        "-of",
        "json",
        str(video_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise MediaError(f"ffprobe failed: {proc.stderr.strip()}")

    data = json.loads(proc.stdout)
    streams = data.get("streams", [])
    fmt = data.get("format", {})

    video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    if not video_stream:
        raise MediaError("No video stream found in file.")

    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    duration = float(fmt.get("duration") or video_stream.get("duration") or 0.0)
    width = int(video_stream.get("width") or 0)
    height = int(video_stream.get("height") or 0)
    fps = _parse_fps(video_stream.get("avg_frame_rate") or video_stream.get("r_frame_rate"))

    if duration <= 0.0 or width <= 0 or height <= 0:
        raise MediaError("Invalid or unreadable video dimensions/duration.")

    return VideoInfo(
        path=video_path,
        duration=duration,
        width=width,
        height=height,
        fps=fps,
        has_audio=has_audio,
    )






def extract_audio_16k_mono(
    video_path: Path,
    output_wav: Path,
    max_duration_sec: Optional[float] = None,
) -> Path:
    """Extract 16 kHz mono 16-bit PCM WAV using FFmpeg."""
    ffmpeg_bin, _ = _find_ffmpeg_binaries()
    output_wav.parent.mkdir(parents=True, exist_ok=True)

    cmd = [ffmpeg_bin, "-y", "-i", str(video_path)]
    if max_duration_sec is not None and max_duration_sec > 0:
        cmd.extend(["-t", f"{max_duration_sec:.3f}"])
    cmd.extend(
        [
            "-vn",
            "-acodec",
            "pcm_s16le",
            "-ar",
            "16000",
            "-ac",
            "1",
            str(output_wav),
        ]
    )
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0 or not output_wav.exists():
        raise MediaError(f"FFmpeg audio extraction failed:\n{proc.stderr.strip()}")
    return output_wav






def _run_concat_batch(
    ffmpeg_bin: str,
    source_video: Path,
    intervals: List[Tuple[float, float]],
    has_audio: bool,
    use_nvenc: bool,
    quality: int,
    output_path: Path,
    progress_callback: Optional[Callable[[float, str], None]] = None,
    batch_total_duration: float = 0.0,
) -> None:
    """Encode and concatenate up to ~25 intervals cleanly with exact A/V sync."""
    # Keep stderr small. With ~25 inputs FFmpeg prints a large stream header
    # block; if that fills an undrained pipe the encoder blocks forever.
    cmd: List[str] = [ffmpeg_bin, "-hide_banner", "-loglevel", "error", "-y"]
    filter_lines: List[str] = []
    concat_inputs: List[str] = []

    video_scale = "scale=trunc(iw/2)*2:trunc(ih/2)*2"

    for idx, (start, end) in enumerate(intervals):
        dur = max(0.05, end - start)
        cmd.extend(
            [
                "-ss",
                f"{start:.3f}",
                "-t",
                f"{dur:.3f}",
                "-i",
                str(source_video),
            ]
        )
        filter_lines.append(
            f"[{idx}:v]{video_scale},setsar=1,format=yuv420p,setpts=PTS-STARTPTS[v{idx}];"
        )
        if has_audio:
            filter_lines.append(
                f"[{idx}:a]aresample=48000:async=1:first_pts=0,aformat=channel_layouts=stereo,asetpts=PTS-STARTPTS[a{idx}];"
            )
            concat_inputs.append(f"[v{idx}][a{idx}]")
        else:
            concat_inputs.append(f"[v{idx}]")

    n = len(intervals)
    if has_audio:
        filter_lines.append(f"{''.join(concat_inputs)}concat=n={n}:v=1:a=1[outv][outa]")
    else:
        filter_lines.append(f"{''.join(concat_inputs)}concat=n={n}:v=1:a=0[outv]")

    # FFmpeg 9 removed -filter_complex_script, so pass the graph inline.
    # Batches are capped at 25 intervals, keeping the command line well within
    # the Windows CreateProcess limit.
    filter_graph = "".join(filter_lines)
    cmd.extend(["-filter_complex", filter_graph, "-map", "[outv]"])
    if has_audio:
        cmd.extend(["-map", "[outa]"])

    if use_nvenc:
        cmd.extend(
            [
                "-c:v",
                "h264_nvenc",
                "-preset",
                "p2",
                "-rc",
                "vbr",
                "-cq",
                str(quality),
                "-b:v",
                "0",
                "-pix_fmt",
                "yuv420p",
            ]
        )
    else:
        cmd.extend(
            [
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-crf",
                str(quality),
                "-pix_fmt",
                "yuv420p",
            ]
        )

    if has_audio:
        cmd.extend(["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"])

    cmd.extend(["-movflags", "+faststart"])

    if progress_callback is None:
        cmd.append(str(output_path))
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            raise MediaError(f"FFmpeg export failed:\n{proc.stderr.strip()}")
        return

    # Stream progress so the UI advances during a long encode instead of
    # appearing frozen between batches. stderr goes to a real file: a PIPE that
    # is only read after the stdout loop deadlocks FFmpeg on large batches.
    cmd.extend(["-progress", "pipe:1", "-nostats", str(output_path)])
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace") as err_file:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=err_file,
            text=True,
            bufsize=1,
        )
        assert proc.stdout is not None
        parsed_ms = 0.0
        try:
            for line in proc.stdout:
                line = line.strip()
                if line.startswith("out_time_ms=") or line.startswith("out_time_us="):
                    try:
                        value = float(line.split("=", 1)[1])
                        parsed_ms = value if "out_time_ms" in line else value / 1000.0
                    except ValueError:
                        pass
                elif line == "progress=end":
                    parsed_ms = max(parsed_ms, batch_total_duration * 1_000_000)
                if batch_total_duration > 0:
                    fraction = min(1.0, parsed_ms / (batch_total_duration * 1_000_000))
                    progress_callback(fraction, f"Encoding with {('h264_nvenc' if use_nvenc else 'libx264')}...")
        finally:
            if proc.poll() is None:
                proc.terminate()
            proc.wait()
            err_file.seek(0)
            err = err_file.read().strip()

    if proc.returncode != 0:
        raise MediaError(f"FFmpeg export failed:\n{err}")


def _render_video(
    video_info: VideoInfo,
    keep_intervals: List[Tuple[float, float]],
    output_path: Path,
    prefer_nvenc: bool,
    quality: int,
    progress_callback: Optional[Callable[[float, str], None]],
) -> Tuple[Path, str]:
    """Render the trimmed timeline at full source quality."""
    ffmpeg_bin, _ = _find_ffmpeg_binaries()
    if not keep_intervals:
        raise MediaError("No KEEP intervals to render.")

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    use_nvenc = prefer_nvenc and detect_nvenc_support()
    encoder_used = "h264_nvenc" if use_nvenc else "libx264"

    batch_size = 25
    interval_durations = [max(0.05, end - start) for start, end in keep_intervals]
    total_duration = sum(interval_durations)

    def batch_progress(batch_index: int, batch_count: int, inner: float) -> None:
        if progress_callback is None:
            return
        if batch_count <= 1:
            progress_callback(inner, f"Encoding with {encoder_used}...")
        else:
            done = sum(interval_durations[: batch_index * batch_size])
            batch_duration = sum(
                interval_durations[batch_index * batch_size : (batch_index + 1) * batch_size]
            )
            fraction = (done + inner * batch_duration) / total_duration if total_duration else 1.0
            progress_callback(fraction, f"Batch {batch_index + 1}/{batch_count} with {encoder_used}...")

    if len(keep_intervals) <= batch_size:
        _run_concat_batch(
            ffmpeg_bin=ffmpeg_bin,
            source_video=video_info.path,
            intervals=keep_intervals,
            has_audio=video_info.has_audio,
            use_nvenc=use_nvenc,
            quality=quality,
            output_path=output_path,
            progress_callback=lambda inner, _msg: batch_progress(0, 1, inner),
            batch_total_duration=total_duration,
        )
        if progress_callback:
            progress_callback(1.0, "Render complete.")
        return output_path, encoder_used

    # Multi-batch render for long lectures with many cuts
    batches = [
        keep_intervals[i : i + batch_size]
        for i in range(0, len(keep_intervals), batch_size)
    ]
    with tempfile.TemporaryDirectory(prefix="lecturetrim_batches_") as tmp_dir:
        tmp_path = Path(tmp_dir)
        batch_files: List[Path] = []
        for idx, batch in enumerate(batches):
            batch_out = tmp_path / f"batch_{idx:04d}.mp4"
            batch_duration = sum(max(0.05, end - start) for start, end in batch)
            _run_concat_batch(
                ffmpeg_bin=ffmpeg_bin,
                source_video=video_info.path,
                intervals=batch,
                has_audio=video_info.has_audio,
                use_nvenc=use_nvenc,
                quality=quality,
                output_path=batch_out,
                progress_callback=lambda inner, _msg, i=idx: batch_progress(i, len(batches), inner),
                batch_total_duration=batch_duration,
            )
            batch_files.append(batch_out)

        if progress_callback:
            progress_callback(0.97, "Merging encoded batches...")

        concat_list = tmp_path / "concat_list.txt"
        with concat_list.open("w", encoding="utf-8") as f:
            for bf in batch_files:
                safe_path = bf.resolve().as_posix().replace("'", "'\\''")
                f.write(f"file '{safe_path}'\n")

        merge_cmd = [
            ffmpeg_bin,
            "-y",
            "-loglevel",
            "error",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_list),
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
        proc = subprocess.run(merge_cmd, capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            raise MediaError(f"FFmpeg final merge failed:\n{proc.stderr.strip()}")

    if progress_callback:
        progress_callback(1.0, "Render complete.")
    return output_path, encoder_used


def export_trimmed_video(
    video_info: VideoInfo,
    keep_intervals: List[Tuple[float, float]],
    output_path: Path,
    prefer_nvenc: bool = True,
    quality: int = 20,
    progress_callback: Optional[Callable[[float, str], None]] = None,
) -> Tuple[Path, str]:
    """Export the final full-quality trimmed MP4. Returns (output_path, encoder)."""
    return _render_video(
        video_info, keep_intervals, output_path, prefer_nvenc, quality,
        progress_callback,
    )


