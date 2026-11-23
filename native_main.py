"""LectureTrim native desktop app.

One window, one local HTTP API, no browser framework. FFmpeg, Silero VAD and
the timeline code are reused from ``lecturetrim``; the UI is a small Hallmark
screen served from ``ui/``. Native file dialogs and drag-and-drop go through
pywebview's JS bridge.
"""

from __future__ import annotations

import json
import os
import re
import socket
import sys
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional

APP_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
UI_DIR = APP_DIR / "ui"
SUPPORTED = {".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v"}

if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from lecturetrim.fastvision import detect_writing_intervals  # noqa: E402
from lecturetrim.media import (  # noqa: E402
    MediaError,
    export_trimmed_video,
    extract_audio_16k_mono,
    probe_video,
)
from lecturetrim.timeline import build_timeline, format_duration  # noqa: E402
from lecturetrim.vad import VadConfig, detect_speech_intervals  # noqa: E402

WORKSPACE = Path(os.environ.get("LECTURETRIM_WORKSPACE", Path.home() / ".lecturetrim"))
EXPORT_DIR = WORKSPACE / "exports"
TEMP_DIR = WORKSPACE / "temp"
SETTINGS_FILE = WORKSPACE / "settings.json"
for directory in (EXPORT_DIR, TEMP_DIR):
    directory.mkdir(parents=True, exist_ok=True)


def _load_settings() -> dict:
    try:
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _remember_folder(path: str) -> None:
    folder = str(Path(path).expanduser().parent)
    settings = _load_settings()
    settings["last_folder"] = folder
    try:
        SETTINGS_FILE.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    except Exception:
        pass


class State:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.video_path: Optional[Path] = None
        self.info = None
        self.timeline: Optional[dict] = None
        self.keep_intervals: list = []
        self.speech_intervals: list = []
        self.visual_intervals: list = []
        self.job: Optional[str] = None
        self.progress = 0.0
        self.message = ""
        self.error: Optional[str] = None
        self.export_path: Optional[Path] = None
        self.export_version = 0
        self.result_note = ""
        self.pending_destination: Optional[str] = None
        self.current_partial: Optional[Path] = None
        self.quality_info: dict = {}

    def set_progress(self, fraction: float, message: str) -> None:
        with self.lock:
            self.progress = max(0.0, min(1.0, float(fraction)))
            self.message = message

    def load_video(self, raw_path: str) -> bool:
        path = Path(raw_path.strip().strip('"').strip("'")).expanduser()
        if not path.is_file() or path.suffix.lower().lstrip(".") not in {e.lstrip(".") for e in SUPPORTED}:
            with self.lock:
                self.error = f"Could not open that video: {path}"
            return False
        try:
            info = probe_video(path)
        except MediaError as exc:
            with self.lock:
                self.error = str(exc)
            return False
        with self.lock:
            self.video_path = path
            self.info = info
            self.timeline = None
            self.keep_intervals = []
            self.export_path = None
            self.export_version = 0
            self.error = None
            self.result_note = ""
            self.progress = 0.0
            self.message = ""
        return True

    def reset(self) -> bool:
        """Return to the home screen without touching the file already saved."""
        with self.lock:
            if self.job is not None:
                return False
            self.video_path = None
            self.info = None
            self.timeline = None
            self.keep_intervals = []
            self.speech_intervals = []
            self.visual_intervals = []
            self.export_path = None
            self.export_version = 0
            self.result_note = ""
            self.pending_destination = None
            self.current_partial = None
            self.quality_info = {}
            self.error = None
            self.progress = 0.0
            self.message = ""
        return True

    def snapshot(self) -> dict:
        with self.lock:
            info = self.info
            return {
                "stage": "ready" if self.timeline else ("loaded" if info else "empty"),
                "file_name": info.path.name if info else "",
                "duration": info.duration if info else 0,
                "width": info.width if info else 0,
                "height": info.height if info else 0,
                "has_audio": info.has_audio if info else False,
                "timeline": self.timeline,
                "result_note": self.result_note,
                "job": self.job,
                "progress": self.progress,
                "message": self.message,
                "error": self.error,
                "export_ready": bool(self.export_path and self.export_path.is_file()),
                "export_version": self.export_version,
                "saved_path": str(self.export_path) if self.export_path else "",
                "quality": dict(self.quality_info),
            }


STATE = State()


def _timeline_dict(result) -> dict:
    return {
        "original_duration": result.original_duration,
        "trimmed_duration": result.trimmed_duration,
        "time_saved": result.time_saved,
        "time_saved_pct": result.time_saved_pct,
        "segments": [
            {
                "status": seg.status,
                "start": seg.start,
                "end": seg.end,
                "duration": seg.duration,
            }
            for seg in result.segments
        ],
    }


def _do_analyze() -> None:
    with STATE.lock:
        info = STATE.info
    if info is None:
        raise RuntimeError("Load a video first.")

    STATE.set_progress(0.02, "Extracting the audio track…")
    wav_path = TEMP_DIR / f"{info.path.stem}_audio.wav"
    extract_audio_16k_mono(info.path, wav_path)

    speech = []
    visual = []
    try:
        def visual_progress(fraction: float, message: str) -> None:
            STATE.set_progress(0.20 + 0.55 * fraction, message)

        with ThreadPoolExecutor(max_workers=2) as pool:
            STATE.set_progress(0.20, "Listening for speech…")
            speech_future = pool.submit(
                detect_speech_intervals, wav_path, info.duration, VadConfig()
            )
            if info.has_audio:
                visual_future = pool.submit(
                    detect_writing_intervals, info.path, info.duration, visual_progress
                )
            else:
                visual_future = None
            speech = speech_future.result()
            visual = visual_future.result() if visual_future else []
    finally:
        wav_path.unlink(missing_ok=True)

    STATE.set_progress(0.82, "Building the trimmed timeline…")
    # A short pause is not a reason to cut. This keeps a teacher who stops for
    # 10 seconds and then resumes, and keeps silent board writing.
    timeline = build_timeline(
        speech_intervals=speech,
        visual_intervals=visual,
        total_duration=info.duration,
        min_gap_to_cut_sec=12.0,
    )
    with STATE.lock:
        STATE.timeline = _timeline_dict(timeline)
        STATE.keep_intervals = timeline.keep_intervals
        STATE.speech_intervals = speech
        STATE.visual_intervals = visual
        STATE.export_path = None
        STATE.export_version = 0
        STATE.result_note = (
            f"Pauses under 12 seconds stay in. Board writing stays in even when "
            f"nobody is speaking. Speech {format_duration(sum(e - s for s, e in speech))}, "
            f"writing {format_duration(sum(e - s for s, e in visual))}."
        )
    STATE.set_progress(1.0, "Analysis complete.")


def _atomic_render() -> None:
    with STATE.lock:
        info = STATE.info
        keep = list(STATE.keep_intervals)
        destination = STATE.pending_destination
    if info is None or not keep:
        raise RuntimeError("Analyze the lecture first.")

    if destination:
        output = Path(destination).expanduser()
        if output.suffix.lower() not in {".mp4", ".mkv", ".mov"}:
            output = output.with_suffix(".mp4")
    else:
        output = EXPORT_DIR / f"{info.path.stem}_trimmed.mp4"
    output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_name(f".{output.stem}.part.mp4")
    partial.unlink(missing_ok=True)
    with STATE.lock:
        STATE.current_partial = partial

    def progress(fraction: float, message: str) -> None:
        STATE.set_progress(fraction, message)

    rendered, encoder_used = export_trimmed_video(
        video_info=info,
        keep_intervals=keep,
        output_path=partial,
        prefer_nvenc=True,
        quality=20,
        progress_callback=progress,
    )

    if not rendered.is_file() or rendered.stat().st_size < 1024:
        raise MediaError("The render did not produce a video file.")
    # Validate before publishing. The UI only shows a player after this passes.
    output_info = probe_video(rendered)
    quality = {
        "encoder": encoder_used,
        "width": output_info.width,
        "height": output_info.height,
        "fps": output_info.fps,
        "source_width": info.width,
        "source_height": info.height,
        "source_fps": info.fps,
        "matches_source": (
            output_info.width == info.width
            and output_info.height == info.height
            and abs(output_info.fps - info.fps) < 0.1
        ),
        "target": "CQ 20 · AAC 192 kbps",
        "audio": "Yes" if info.has_audio else "No source audio",
    }
    os.replace(rendered, output)

    with STATE.lock:
        STATE.quality_info = quality
        STATE.export_path = output
        STATE.export_version = int(time.time())
    STATE.set_progress(1.0, "Video ready.")


def run_job(name: str, destination: str | None = None) -> None:
    with STATE.lock:
        if STATE.job is not None:
            return
        STATE.pending_destination = destination
        STATE.job = name
        STATE.progress = 0.0
        STATE.message = "Starting…"
        STATE.error = None
    try:
        if name == "analyze":
            _do_analyze()
        elif name == "export":
            _atomic_render()
        else:
            raise ValueError(f"Unknown job: {name}")
    except Exception as exc:  # surfaced in the UI, never a silent freeze
        with STATE.lock:
            STATE.error = str(exc)
            STATE.message = ""
    finally:
        with STATE.lock:
            STATE.job = None
            STATE.pending_destination = None
            partial = STATE.current_partial
            STATE.current_partial = None
        if partial is not None:
            partial.unlink(missing_ok=True)


class AppHandler(BaseHTTPRequestHandler):
    server_version = "LectureTrim/1.0"

    def log_message(self, fmt: str, *args) -> None:
        return

    def _send(self, status: int, body: bytes, content_type: str, extra: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload: dict, status: int = 200) -> None:
        self._send(status, json.dumps(payload).encode("utf-8"), "application/json; charset=utf-8")

    def _body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            return json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            return {}

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path == "/api/status":
            self._json(STATE.snapshot())
            return
        if path == "/api/video":
            self._serve_video(urllib.parse.parse_qs(parsed.query))
            return

        files = {
            "/": UI_DIR / "index.html",
            "/index.html": UI_DIR / "index.html",
            "/app.js": UI_DIR / "app.js",
            "/styles.css": UI_DIR / "styles.css",
            "/poster.svg": UI_DIR / "poster.svg",
            "/base.css": APP_DIR / "base.css",
            "/tokens.css": APP_DIR / "tokens.css",
        }
        target = files.get(path)
        if target is None or not target.is_file():
            self._send(404, b"Not found", "text/plain")
            return
        content_type = "text/html; charset=utf-8" if target.suffix == ".html" else (
            "text/css; charset=utf-8" if target.suffix == ".css" else
            "image/svg+xml" if target.suffix == ".svg" else
            "application/javascript; charset=utf-8"
        )
        self._send(200, target.read_bytes(), content_type)

    def _serve_video(self, query: dict) -> None:
        with STATE.lock:
            target = STATE.export_path
        if target is None or not target.is_file():
            self._send(404, b"Not found", "text/plain")
            return
        size = target.stat().st_size
        range_header = self.headers.get("Range", "")
        match = re.match(r"bytes=(\d+)-(\d*)", range_header)
        status = 200
        extra = {}
        if match:
            start = int(match.group(1))
            end = int(match.group(2)) if match.group(2) else size - 1
            end = min(end, size - 1)
            status = 206
            extra = {"Content-Range": f"bytes {start}-{end}/{size}"}
        else:
            start, end = 0, size - 1
        length = max(0, end - start + 1)
        self.send_response(status)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        for key, value in extra.items():
            self.send_header(key, value)
        self.end_headers()
        with target.open("rb") as handle:
            handle.seek(start)
            remaining = length
            while remaining > 0:
                chunk = handle.read(min(256 * 1024, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def do_POST(self) -> None:  # noqa: N802
        path = urllib.parse.urlparse(self.path).path
        body = self._body()
        if path == "/api/load":
            ok = STATE.load_video(str(body.get("path", "")))
            self._json({"ok": ok, "error": STATE.error if not ok else None})
            return
        if path == "/api/reset":
            ok = STATE.reset()
            self._json({"ok": ok, "error": None if ok else "The current job is still running."})
            return
        if path in ("/api/analyze", "/api/export"):
            name = path.rsplit("/", 1)[1]
            destination = body.get("destination") if name == "export" else None
            threading.Thread(target=run_job, args=(name, destination), daemon=True).start()
            self._json({"started": name})
            return
        self._send(404, b"Not found", "text/plain")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class DesktopApi:
    """Called from the page as ``window.pywebview.api``."""

    def __init__(self) -> None:
        self._window = None  # public attributes are introspected; keep the Window private
        self._base_url = ""

    def open_video(self) -> str:
        if self._window is None:
            return ""
        import webview

        selected = self._window.create_file_dialog(
            webview.FileDialog.OPEN,
            allow_multiple=False,
            file_types=(
                "Video files (*.mp4;*.mkv;*.mov;*.webm;*.avi;*.m4v)",
                "All files (*.*)",
            ),
        )
        if not selected:
            return ""
        path = str(selected[0] if isinstance(selected, (tuple, list)) else selected)
        _remember_folder(path)
        STATE.load_video(path)
        return path

    def drop_by_name(self, name: str) -> str:
        from webview.dom import _dnd_state

        for item in list(_dnd_state["paths"]):
            if item[0] == name:
                _dnd_state["paths"].remove(item)
                STATE.load_video(item[1])
                return item[1]
        return ""

    def choose_destination(self) -> str:
        """Ask where the export should be written *before* it starts."""
        if self._window is None:
            return ""
        with STATE.lock:
            info = STATE.info
            last_folder = _load_settings().get("last_folder", "")
        stem = info.path.stem if info else "lecture"
        import webview

        selected = self._window.create_file_dialog(
            webview.FileDialog.SAVE,
            directory=last_folder if isinstance(last_folder, str) else "",
            save_filename=f"{stem}_trimmed.mp4",
            file_types=("MP4 video (*.mp4)", "Matroska video (*.mkv)", "All files (*.*)"),
        )
        if not selected:
            return ""
        target = str(selected[0] if isinstance(selected, (tuple, list)) else selected)
        _remember_folder(target)
        return target


def main() -> None:
    if "--serve-only" in sys.argv:
        port = int(os.environ.get("LECTURETRIM_PORT", "8765"))
        server = ThreadingHTTPServer(("127.0.0.1", port), AppHandler)
        print(f"LECTURETRIM_SERVING={port}", flush=True)
        server.serve_forever()
        return

    import webview
    from webview.dom import _dnd_state

    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    server = ThreadingHTTPServer(("127.0.0.1", port), AppHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    _dnd_state["num_listeners"] += 1
    api = DesktopApi()
    api._base_url = base_url
    window = webview.create_window(
        "LectureTrim",
        base_url,
        js_api=api,
        width=1280,
        height=820,
        min_size=(920, 620),
        confirm_close=False,
    )
    api._window = window
    window.events.closing += lambda: os._exit(0)
    webview.start()


if __name__ == "__main__":
    main()
