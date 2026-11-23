# LectureTrim

LectureTrim is a local Windows desktop app that shortens long lecture recordings. It removes dead time while keeping speech and on-screen board writing, then writes a trimmed video to the location you choose. Nothing is uploaded and no account is required.

## Features

- Local speech detection with Silero VAD.
- Keeps board writing even when the room is silent.
- Preserves pauses shorter than **12 seconds**, so a teacher thinking or walking to the board is not cut off.
- Streams FFmpeg export progress and verifies the finished file before showing it.
- Saves directly to a native **Save As** destination; no draft render or temporary library.
- **Analyze another video** returns to the home screen without deleting the file you just saved.
- Single-screen native window (pywebview); no local server to manage and no browser framework.

## Requirements

### Installed app

- Windows 10 or 11, 64-bit.
- About 1 GB of free disk space for the portable exe and its temporary extraction.
- An MP4, MKV, MOV, WebM, AVI, or M4V input file.

The packaged build includes Python, FFmpeg, FFprobe, and the speech model. Nothing else needs to be installed.

### Running from source

- **Python 3.11, 64-bit** (the version this build is tested with).
- **FFmpeg and FFprobe on `PATH`.** On Windows:

  ```bat
  winget install Gyan.FFmpeg
  ```

  Open a new terminal afterward and confirm both commands work:

  ```bat
  ffmpeg -version
  ffprobe -version
  ```

- A few GB of free disk space for the virtual environment and PyTorch.

## Install the desktop app

Build the executable first, then install it:

```bat
build_windows.bat
install_windows.bat
```

`build_windows.bat` creates `.venv`, installs the pinned dependencies, and produces `dist\LectureTrim.exe`.

`install_windows.bat` copies that exe to:

```text
%LOCALAPPDATA%\Programs\LectureTrim\LectureTrim.exe
```

It also creates shortcuts on the Desktop and in the Start Menu. After installation, type **LectureTrim** in Windows search to open it.

To update an existing installation, run both scripts again. The installed copy is replaced in place.

## Run from source

The quickest path is:

```bat
run.bat
```

`run.bat` creates `.venv` if needed, installs missing dependencies, and launches the app with `pythonw.exe` so no console stays open.

Manual setup:

```bat
py -3.11 -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python native_main.py
```

FFmpeg and FFprobe must be on `PATH`. The packaged exe bundles them; a source run does not.

## Use

1. Open a lecture with **Choose a video** (or `Ctrl+O`), or drag it onto the window.
2. Click **Analyze lecture**. Speech and visual activity are detected locally.
3. Review the estimated result, then click **Save & export MP4**.
4. Pick a destination in the native Save dialog. Export starts only after a destination is chosen.
5. When rendering finishes, the result card shows resolution, frame rate, encoder, and the saved path.
6. Click **Analyze another video** to return home. The exported file is left untouched.

Pauses of **12 seconds or more** may be removed. Shorter pauses are kept. Segments with board writing are kept even when nobody is speaking.

## How trimming works

- **Speech:** Silero VAD runs at 16 kHz with conservative settings: threshold `0.35`, `0.6 s` pre/post padding, `250 ms` minimum speech, and `300 ms` silence tolerance.
- **Board writing:** one FFmpeg process emits `1 fps`, `320x180` grayscale frames. Frame differences identify visual activity cheaply enough for multi-hour lectures.
- **Timeline:** speech and visual intervals are merged, then gaps shorter than `12 seconds` are preserved.
- **Export:** FFmpeg renders to a temporary file beside the destination, probes the result, then atomically renames it. Long timelines are encoded in batches of 25 intervals and merged by stream copy.

The app is intentionally conservative: it prefers keeping a quiet moment over cutting speech or writing.

## Local data and privacy

Everything runs on the machine. There are no uploads, telemetry, accounts, or cloud services.

Working files live in:

```text
%USERPROFILE%\.lecturetrim\
```

- `exports\` — fallback export location when no destination is chosen.
- `temp\` — extracted audio and intermediate render files. These are cleaned up after a job.
- `settings.json` — remembers the last folder used in file dialogs.

The video you choose is read in place. The exported file is written only to the destination you select.

## Build a portable exe

```bat
build_windows.bat
```

The result is a single file:

```text
dist\LectureTrim.exe
```

The build bundles FFmpeg, FFprobe, the Silero model, and PyTorch. For that reason the exe is large, and PyInstaller must extract it on every launch. On the current test machine the window takes roughly **30 seconds** to appear; the app is still a single portable file. This startup cost does not change analysis or export results.

Build prerequisites:

- Windows 64-bit.
- Python 3.11 available as `py` or `python`.
- FFmpeg and FFprobe on `PATH` at build time (the spec copies those binaries into the bundle).
- Several GB of free disk space and a few minutes for PyInstaller.

To build without the helper script:

```bat
.venv\Scripts\python.exe -m PyInstaller --clean --noconfirm LectureTrim.spec
```

## Project layout

```text
native_main.py            Native window, local API, job state, reset and export flow
lecturetrim/
  media.py                FFmpeg/FFprobe probing, extraction, rendering, atomic rename
  vad.py                  Torch-based Silero speech detection
  fastvision.py           1 fps grayscale visual-activity detection
  timeline.py             Interval merging and the 12-second pause rule
ui/
  index.html              Single-screen interface
  app.js                  UI state and API calls
  styles.css              Screen styles
  poster.svg              Home-screen artwork
tokens.css                Design tokens shared by the UI
base.css                  Base layout and shared component styles
LectureTrim.spec          One-file PyInstaller definition
build_windows.bat         Creates .venv, installs dependencies, builds the exe
install_windows.bat       Copies the exe and creates Desktop/Start Menu shortcuts
run.bat                   Source launcher with automatic dependency setup
requirements.txt          Pinned Python dependencies
```

## Development configuration

These environment variables are useful when testing without the desktop window:

| Variable | Default | Purpose |
| --- | --- | --- |
| `LECTURETRIM_WORKSPACE` | `%USERPROFILE%\.lecturetrim` | Working directory for exports, temp files, and settings. |
| `LECTURETRIM_PORT` | `8765` | Port used with `--serve-only`. |

Start the local API without opening a window:

```bat
.venv\Scripts\python.exe native_main.py --serve-only
```

Then open `http://127.0.0.1:8765/`. This is a loopback-only development mode; normal use launches the native window.

## Troubleshooting

**The window takes about 30 seconds to appear.**
This is expected for the one-file build. PyInstaller extracts the bundled runtime on every launch. Do not close the app during this wait; the window appears when extraction finishes.

**Windows SmartScreen warns about the exe.**
The build is unsigned. Use **More info → Run anyway** only if you built it yourself or trust the source.

**`FFmpeg not found` when running from source.**
Install FFmpeg, open a new terminal, and check that `ffmpeg -version` and `ffprobe -version` both work. The packaged exe does not need this.

**`ModuleNotFoundError` or a missing PyTorch/Silero dependency.**
Reinstall the pinned dependencies:

```bat
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

**Analysis is slow on a long lecture.**
Speech detection and visual scanning run sequentially and report progress in the window. A two-hour lecture takes a few minutes; this is processing time, not startup time.

**Export is slow or appears stuck.**
A full-resolution export is a real encode. The button stays disabled until FFmpeg finishes and the output has been verified.

**Export fails.**
Check that the destination folder exists and is writable, and that there is enough free disk space for the rendered file.

**LectureTrim is missing from Windows search.**
Run `install_windows.bat` again after building, then give Windows a few seconds to index the new Start Menu shortcut.

**A codec cannot be opened.**
The packaged build uses the FFmpeg binaries available at build time. If a source run rejects a file, install a full FFmpeg build (for example, Gyan's `full_build`) and rebuild.
