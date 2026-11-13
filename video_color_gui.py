"""Video and image color correction GUI using Tkinter, OpenCV, and FFmpeg.

Run: python video_color_gui.py

The GUI applies the actual full-resolution color pipeline for accurate
previews, then downsizes only for display. The baseline HueSatVal3.py is unchanged.
"""

from __future__ import annotations

import json
import math
import os
import queue
import shutil
import subprocess
import tempfile
import threading
import time
import tkinter as tk
from dataclasses import asdict
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Callable

import cv2
from PIL import Image, ImageTk

from region_inspector import RegionInspector
from color_pipeline import (
    AutoExposureSettings,
    ColorSettings,
    calculate_exposure_curve,
    measure_subject_level,
    subject_adjusted_settings,
    hex_to_bgr,
    measure_balance_offset,
    process_frame,
)


# Name, minimum, maximum, neutral/default value, slider precision.
CONTROL_SPECS = (
    ("brightness", -255.0, 255.0, 0.0, 0.1),
    ("contrast", 0.0, 3.0, 1.0, 0.01),
    ("exposure", -3.0, 3.0, 0.0, 0.01),
    ("saturation", 0.0, 3.0, 1.0, 0.01),
    ("vibrance", -1.0, 1.0, 0.0, 0.01),
    ("highlights", -5.0, 5.0, 0.0, 0.01),
    ("shadows", -5.0, 5.0, 0.0, 0.01),
    ("gamma", 0.2, 3.0, 1.0, 0.01),
    ("sharpness", 0.0, 2.0, 0.0, 0.01),
    ("blue_gain", 0.0, 8.0, 1.0, 0.001),
    ("green_gain", 0.0, 8.0, 1.0, 0.001),
    ("red_gain", 0.0, 8.0, 1.0, 0.001),
    ("hue_low", 0.0, 179.0, 95.0, 1.0),
    ("hue_high", 0.0, 179.0, 135.0, 1.0),
)


def prepare_preview_frame(
    frame,
    settings: ColorSettings,
    max_width: int = 535,
    max_height: int = 355,
    extra_stops: float = 0.0,
):
    """Process original-resolution pixels before resizing for display.

    Resizing first changes nonlinear operations (HSV thresholding, highlights,
    sharpening, etc.), so it cannot represent the actual export reliably.
    Keeping processing order identical makes the preview an accurate look at
    the saved image/video apart from codec compression and display color space.
    """
    adjusted = subject_adjusted_settings(settings, extra_stops)
    corrected = process_frame(frame, adjusted)
    height, width = frame.shape[:2]
    scale = min(max_width / width, max_height / height, 1.0)
    if scale >= 1.0:
        return frame, corrected
    dimensions = (max(1, round(width * scale)), max(1, round(height * scale)))
    original_small = cv2.resize(frame, dimensions, interpolation=cv2.INTER_AREA)
    corrected_small = cv2.resize(
        corrected, dimensions, interpolation=cv2.INTER_AREA
    )
    return original_small, corrected_small


def validate_number(name: str, text: str) -> float:
    """Parse exact typed values without rounding to slider resolution."""
    try:
        value = float(text.strip())
    except ValueError as exc:
        raise ValueError(f"{name.replace('_', ' ')} must be a number.") from exc
    if not math.isfinite(value):
        raise ValueError(f"{name.replace('_', ' ')} must be finite.")
    spec = next((item for item in CONTROL_SPECS if item[0] == name), None)
    if spec is None:
        raise KeyError(f"Unknown correction: {name}")
    _, low, high, _, _ = spec
    # A sampled dark calibration patch can produce gains above slider max 8.
    # Maintain the v1.2 behavior that expanded those gains as needed.
    if name in ("blue_gain", "green_gain", "red_gain"):
        high = 255.0
    if not low <= value <= high:
        raise ValueError(
            f"{name.replace('_', ' ')} must be between {low:g} and {high:g}."
        )
    if name in ("hue_low", "hue_high") and not value.is_integer():
        raise ValueError(f"{name.replace('_', ' ')} must be an integer.")
    return value


def _launch_cancellable_ffmpeg(
    command: list[str],
    stop_event: threading.Event,
    error_log,
    *,
    accept_video_frames: bool = False,
) -> tuple[subprocess.Popen, threading.Event, threading.Thread]:
    """Run FFmpeg, with a watcher that can interrupt blocked pipe writes.

    Setting a cancellation flag alone does not interrupt a Python thread
    blocked in stdin.write(). Killing the encoder releases that blocked writer.
    """
    if stop_event.is_set():
        raise InterruptedError("Export canceled")

    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE if accept_video_frames else subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=error_log,
        bufsize=0,
    )
    watcher_done = threading.Event()

    def watch_cancel() -> None:
        while not watcher_done.is_set():
            if stop_event.wait(0.1):
                if not watcher_done.is_set() and process.poll() is None:
                    process.kill()
                return

    watcher = threading.Thread(
        target=watch_cancel, name="FFmpeg cancellation monitor", daemon=True
    )
    watcher.start()
    return process, watcher_done, watcher


def _end_ffmpeg_process(
    process: subprocess.Popen,
    watcher_done: threading.Event,
    watcher: threading.Thread,
) -> None:
    """Close an encoder safely, including when export has been canceled."""
    watcher_done.set()
    if process.stdin is not None and not process.stdin.closed:
        try:
            process.stdin.close()
        except OSError:
            pass
    if process.poll() is None:
        process.kill()
    process.wait()
    watcher.join(timeout=0.5)


def _ffmpeg_error(error_log, prefix: str, exit_code: int) -> RuntimeError:
    """Include the useful tail of FFmpeg's diagnostic output in the error."""
    error_log.seek(0)
    detail = error_log.read().decode("utf-8", errors="replace").strip()
    return RuntimeError(
        f"{prefix} (exit {exit_code}): "
        f"{detail[-1600:] or 'No diagnostic information was returned.'}"
    )



def analyze_video_levels(
    source: Path,
    roi: tuple[float, float, float, float],
    cancel_event: threading.Event | None = None,
    report: Callable[[int, int], None] | None = None,
) -> list[float]:
    """First pass: read subject brightness from each frame in source order.

    Decoding is required even though only a small ROI is measured. Using this
    deterministic analysis for both preview and export avoids different
    exposure values when the user seeks backward in the timeline.
    """
    capture = cv2.VideoCapture(str(source))
    if not capture.isOpened():
        raise RuntimeError(f"Unable to open video for analysis: {source}")
    try:
        total = max(0, int(capture.get(cv2.CAP_PROP_FRAME_COUNT)))
        levels = []
        while True:
            if cancel_event is not None and cancel_event.is_set():
                raise InterruptedError("Exposure analysis canceled")
            ok, frame = capture.read()
            if not ok:
                break
            levels.append(measure_subject_level(frame, roi))
            if report is not None and (len(levels) == 1 or len(levels) % 30 == 0):
                report(len(levels), total)
        if not levels:
            raise RuntimeError("Could not analyze any video frames.")
        if report is not None:
            report(len(levels), total)
        return levels
    finally:
        capture.release()


def save_corrected_image(
    source: Path,
    destination: Path,
    settings: ColorSettings,
    auto: AutoExposureSettings | None = None,
) -> None:
    """Load an image, apply the shared pipeline, and save the corrected output."""
    source = Path(source)
    destination = Path(destination)
    if not source.is_file():
        raise FileNotFoundError(f"Source image does not exist: {source}")
    if not destination.parent.is_dir():
        raise FileNotFoundError(f"Output folder does not exist: {destination.parent}")
    image = cv2.imread(str(source), cv2.IMREAD_UNCHANGED)
    if image is None:
        raise RuntimeError(f"Unable to read image: {source}")
    if auto is not None and auto.enabled:
        level = measure_subject_level(image, auto.roi)
        extra_stops = calculate_exposure_curve([level], 1.0, auto)[0]
        settings = subject_adjusted_settings(settings, extra_stops)
    corrected = process_frame(image, settings)
    if not cv2.imwrite(str(destination), corrected):
        raise RuntimeError(f"Unable to write image: {destination}")


def export_video(
    source: Path,
    destination: Path,
    settings: ColorSettings,
    stop_event: threading.Event,
    report: Callable[[int, int], None],
    report_stage: Callable[[str], None] | None = None,
    auto: AutoExposureSettings | None = None,
    precomputed_levels: list[float] | None = None,
) -> None:
    """Export corrected video in two independent, cancellable stages.

    Stage 1 encodes corrected frames into a temporary video-only MP4.
    Stage 2 copies that video into a new MP4 with audio from the source.
    The source and output are NEVER opened as FFmpeg inputs concurrently
    with our raw frame writer, avoiding pipeline interleaving stalls.

    Work is staged in the destination folder, so canceled or failed jobs do
    not overwrite an existing output. The final rename is atomic on the same
    filesystem. The original video is read-only and never modified.
    """
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError(
            "FFmpeg not found. Install FFmpeg and add ffmpeg.exe to PATH."
        )
    source = Path(source)
    destination = Path(destination)
    if source.resolve() == destination.resolve():
        raise ValueError("Output must not overwrite the input video.")
    if not source.is_file():
        raise FileNotFoundError(f"Source video does not exist: {source}")
    if not destination.parent.is_dir():
        raise FileNotFoundError(f"Export folder does not exist: {destination.parent}")

    def stage(message: str) -> None:
        if report_stage is not None:
            report_stage(message)

    capture = cv2.VideoCapture(str(source))
    if not capture.isOpened():
        raise RuntimeError(f"Unable to open video: {source}")

    try:
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = capture.get(cv2.CAP_PROP_FPS)
        total = max(0, int(capture.get(cv2.CAP_PROP_FRAME_COUNT)))
        if width <= 0 or height <= 0 or not 0 < fps < 1000:
            raise RuntimeError("Could not read valid source dimensions/FPS.")

        exposure_curve = None
        if auto is not None and auto.enabled:
            levels = precomputed_levels
            if levels is None:
                stage("Analyzing subject lighting")
                levels = analyze_video_levels(
                    source, auto.roi, cancel_event, report
                )
            exposure_curve = calculate_exposure_curve(levels, fps, auto)

        with tempfile.TemporaryDirectory(
            prefix=".huesatval_export_", dir=str(destination.parent)
        ) as workspace:
            workdir = Path(workspace)
            silent_video = workdir / "corrected_video.mp4"
            finished_video = workdir / "video_with_audio.mp4"

            encode_command = [
                ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                "-f", "rawvideo", "-pix_fmt", "bgr24",
                "-s", f"{width}x{height}", "-r", f"{fps:.10f}",
                "-i", "pipe:0", "-an",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                "-threads", "4",
                # Explicit signaling lets players interpret encoded SDR colors
                # with the same BT.709 matrix used during RGB-to-YUV conversion.
                "-x264-params",
                "colorprim=bt709:transfer=bt709:colormatrix=bt709",
                "-vf", (
                    "pad=ceil(iw/2)*2:ceil(ih/2)*2,"
                    "scale=in_range=full:out_range=limited:"
                    "out_color_matrix=bt709,format=yuv420p"
                ),
                "-pix_fmt", "yuv420p",
                "-color_primaries", "bt709",
                "-color_trc", "bt709",
                "-colorspace", "bt709",
                "-color_range", "tv",
                str(silent_video),
            ]
            stage("Encoding corrected video")
            count = 0
            with tempfile.TemporaryFile(mode="w+b") as errors:
                process, done, watcher = _launch_cancellable_ffmpeg(
                    encode_command, stop_event, errors, accept_video_frames=True
                )
                try:
                    while not stop_event.is_set():
                        ok, frame = capture.read()
                        if not ok:
                            break
                        if exposure_curve is not None:
                            # Decode counts may occasionally differ from the
                            # reported frame count; handle a rare extra frame.
                            index = min(count, len(exposure_curve) - 1)
                            frame_settings = subject_adjusted_settings(
                                settings, exposure_curve[index]
                            )
                        else:
                            frame_settings = settings
                        corrected = process_frame(frame, frame_settings)
                        if process.poll() is not None:
                            if stop_event.is_set():
                                raise InterruptedError("Export canceled")
                            raise _ffmpeg_error(
                                errors, "Video encoder stopped unexpectedly",
                                process.returncode,
                            )
                        if process.stdin is None:
                            raise RuntimeError("Encoder input pipe is unavailable.")
                        process.stdin.write(corrected.tobytes())
                        count += 1
                        if count == 1 or count % 5 == 0:
                            report(count, total)

                    if stop_event.is_set():
                        raise InterruptedError("Export canceled")
                    if count == 0:
                        raise RuntimeError("Source contains no decodable frames.")
                    if process.stdin is not None:
                        process.stdin.close()
                    return_code = process.wait()
                    if stop_event.is_set():
                        raise InterruptedError("Export canceled")
                    if return_code != 0:
                        raise _ffmpeg_error(
                            errors, "Video encoding failed", return_code
                        )
                    report(count, total)
                except OSError as exc:
                    if stop_event.is_set():
                        raise InterruptedError("Export canceled") from exc
                    raise RuntimeError(
                        f"Unable to send frames to video encoder: {exc}"
                    ) from exc
                finally:
                    _end_ffmpeg_process(process, done, watcher)

            capture.release()
            if stop_event.is_set():
                raise InterruptedError("Export canceled")

            stage("Muxing original audio and finalizing MP4")
            mux_command = [
                ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                "-i", str(silent_video), "-i", str(source),
                "-map", "0:v:0", "-map", "1:a?",
                "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                "-movflags", "+faststart",
                str(finished_video),
            ]
            with tempfile.TemporaryFile(mode="w+b") as errors:
                process, done, watcher = _launch_cancellable_ffmpeg(
                    mux_command, stop_event, errors
                )
                try:
                    return_code = process.wait()
                    if stop_event.is_set():
                        raise InterruptedError("Export canceled")
                    if return_code != 0:
                        raise _ffmpeg_error(
                            errors, "Audio muxing failed", return_code
                        )
                finally:
                    _end_ffmpeg_process(process, done, watcher)

            if stop_event.is_set():
                raise InterruptedError("Export canceled")
            os.replace(finished_video, destination)
            stage("Export complete")
    finally:
        capture.release()


class VideoColorApp:
    """Preview and export video or save still images using shared corrections."""

    PREVIEW_WIDTH = 535
    PREVIEW_HEIGHT = 355

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("PicVidFix Video & Image Lab v1.5.1")
        self.root.geometry("1490x900")
        self.root.minsize(1160, 680)

        self.source_path: Path | None = None
        self.media_kind: str | None = None  # "video" | "image" | None
        self.capture: cv2.VideoCapture | None = None
        self.source_frame = None
        self.current_frame = 0
        self.frame_count = 0
        self.fps = 30.0
        self.playing = False
        self.play_job: str | None = None
        self.preview_job: str | None = None
        self.seek_job: str | None = None
        self.setting_timeline = False

        self.export_thread: threading.Thread | None = None
        self.cancel_event = threading.Event()
        self.events: queue.Queue = queue.Queue()
        self.exporting = False
        self.export_started_at = 0.0
        self.export_last_update_at = 0.0
        self.export_stage = ""
        self.export_progress = ""

        self.values = {
            name: tk.DoubleVar(value=default)
            for name, _, _, default, _ in CONTROL_SPECS
        }
        self.controls: dict[str, tk.Scale] = {}
        self.spinboxes: dict[str, ttk.Spinbox] = {}
        # Text is deliberately NOT bound directly to the scale's DoubleVar.
        # Tk Scale rejects partial numeric input and rounds to its resolution.
        self.numeric_text = {
            name: tk.StringVar(value=f"{default:g}")
            for name, _, _, default, _ in CONTROL_SPECS
        }
        self.preview_generation = 0
        self.preview_busy = False
        self.preview_pending = None
        self.closing = False
        self.remap_enabled = tk.BooleanVar(value=False)
        self.remap_hex = tk.StringVar(value="FF8800")
        self.calibration_hex = tk.StringVar(value="FFFFFF")
        self.status = tk.StringVar(value="Open a video or image to begin.")
        self.timeline_text = tk.StringVar(value="00:00 / 00:00")
        self.timeline_var = tk.DoubleVar(value=0)
        self.preview_hint = tk.StringVar(
            value="Open a video or image. Video preview playback is silent; exports keep original audio."
        )
        # Region inspection is an annotation, independent of color settings.
        self.region_box: tuple[int, int, int, int] | None = None
        self.pixel_position = tk.StringVar(value="X: —  Y: —")
        # Subject-priority auto exposure works across successive frames.
        # The selected ROI is normalized to source image dimensions.
        self.auto_enabled = tk.BooleanVar(value=False)
        self.auto_roi = AutoExposureSettings().roi
        self.auto_numbers = {
            "target": tk.StringVar(value="135"),
            "strength": tk.StringVar(value="1"),
            "max_stops": tk.StringVar(value="1.5"),
            "smoothing_seconds": tk.StringVar(value="0.65"),
        }
        self.auto_info = tk.StringVar(value="Off. Background does not affect metering.")
        self.auto_levels: list[float] | None = None
        self.auto_levels_key = None
        self.auto_curve: list[float] | None = None
        self.auto_curve_key = None
        self.auto_analysis_thread: threading.Thread | None = None
        self.auto_analysis_cancel = threading.Event()
        self.auto_analysis_token = 0
        self.auto_analyzing = False
        self.auto_pending_key = None

        self._build_ui()
        self._sync_media_buttons()
        self.root.after(100, self._poll_events)
        self.root.protocol("WM_DELETE_WINDOW", self._close)

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=10)
        outer.pack(fill="both", expand=True)

        header = ttk.Frame(outer)
        header.pack(fill="x", pady=(0, 8))
        ttk.Button(header, text="Open video", command=self._open_video).pack(
            side="left", padx=(0, 6)
        )
        ttk.Button(header, text="Open image", command=self._open_image).pack(
            side="left", padx=(0, 10)
        )
        ttk.Button(header, text="Save preset", command=self._save_preset).pack(
            side="left", padx=(0, 6)
        )
        ttk.Button(header, text="Load preset", command=self._load_preset).pack(
            side="left", padx=(0, 6)
        )
        # The original reset lived at the bottom of the scrolling sidebar.
        # Keep a persistent, easy-to-find reset in the main toolbar.
        ttk.Button(header, text="Reset all", command=self._reset).pack(
            side="left", padx=(0, 10)
        )
        self.save_image_button = ttk.Button(
            header, text="Save image", command=self._save_image
        )
        self.save_image_button.pack(side="left", padx=(0, 6))
        self.export_button = ttk.Button(
            header, text="Export MP4", command=self._start_export
        )
        self.export_button.pack(side="left", padx=(0, 6))
        self.cancel_button = ttk.Button(
            header,
            text="Cancel export",
            command=self._cancel_export,
            state="disabled",
        )
        self.cancel_button.pack(side="left")
        ttk.Button(
            header, text="Inspect / Draw box", command=self._inspect_region
        ).pack(side="left", padx=(10, 0))

        main = ttk.Frame(outer)
        main.pack(fill="both", expand=True)
        preview_area = ttk.Frame(main)
        preview_area.pack(side="left", fill="both", expand=True)
        controls_area = ttk.Frame(main, width=360)
        controls_area.pack(side="right", fill="y", padx=(14, 0))
        controls_area.pack_propagate(False)

        compare = ttk.Frame(preview_area)
        compare.pack(fill="both", expand=True)
        left = ttk.LabelFrame(compare, text="Original", padding=5)
        right = ttk.LabelFrame(compare, text="Corrected preview", padding=5)
        left.pack(side="left", fill="both", expand=True, padx=(0, 4))
        right.pack(side="left", fill="both", expand=True, padx=(4, 0))
        self.original_label = ttk.Label(left, text="No media loaded", anchor="center")
        self.adjusted_label = ttk.Label(right, text="No media loaded", anchor="center")
        self.original_label.pack(fill="both", expand=True)
        self.adjusted_label.pack(fill="both", expand=True)
        self.original_photo = None
        self.adjusted_photo = None
        self.original_label.bind(
            "<Motion>", lambda event: self._hover_preview_pixel(event, True)
        )
        self.adjusted_label.bind(
            "<Motion>", lambda event: self._hover_preview_pixel(event, False)
        )
        for label in (self.original_label, self.adjusted_label):
            label.bind(
                "<Leave>", lambda _event: self.pixel_position.set("X: —  Y: —")
            )

        player = ttk.Frame(preview_area)
        player.pack(fill="x", pady=(10, 0))
        self.play_button = ttk.Button(player, text="Play", command=self._toggle_play)
        self.play_button.pack(side="left")
        ttk.Label(player, textvariable=self.timeline_text, width=18).pack(
            side="right"
        )
        ttk.Label(
            player, textvariable=self.pixel_position, width=24
        ).pack(side="right", padx=(0, 8))
        self.timeline = ttk.Scale(
            player,
            from_=0,
            to=1,
            variable=self.timeline_var,
            command=self._timeline_changed,
        )
        self.timeline.pack(side="left", fill="x", expand=True, padx=8)
        ttk.Label(
            preview_area,
            textvariable=self.preview_hint,
            foreground="#666666",
            wraplength=900,
            justify="left",
        ).pack(anchor="w", pady=(8, 0))

        self.progress_bar = ttk.Progressbar(outer, mode="determinate")
        self.progress_bar.pack(fill="x", pady=(10, 2))
        ttk.Label(outer, textvariable=self.status, anchor="w").pack(fill="x")

        scroll_canvas = tk.Canvas(controls_area, highlightthickness=0)
        scroll = ttk.Scrollbar(
            controls_area, orient="vertical", command=scroll_canvas.yview
        )
        scroll_canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        scroll_canvas.pack(side="left", fill="both", expand=True)
        control_host = ttk.Frame(scroll_canvas, padding=(4, 4, 10, 8))
        window_id = scroll_canvas.create_window(
            (0, 0), anchor="nw", window=control_host
        )
        control_host.bind(
            "<Configure>",
            lambda _: scroll_canvas.configure(scrollregion=scroll_canvas.bbox("all")),
        )
        scroll_canvas.bind(
            "<Configure>",
            lambda event: scroll_canvas.itemconfigure(window_id, width=event.width),
        )

        ttk.Label(
            control_host, text="Subject exposure (dynamic)",
            font=("Segoe UI", 12, "bold"),
        ).pack(anchor="w", pady=(0, 5))
        ttk.Checkbutton(
            control_host, text="Enable subject-priority exposure",
            variable=self.auto_enabled, command=self._auto_changed,
        ).pack(anchor="w")
        auto_actions = ttk.Frame(control_host)
        auto_actions.pack(fill="x", pady=(4, 5))
        ttk.Button(
            auto_actions, text="Select subject area...",
            command=self._select_subject_area,
        ).pack(side="left")
        self.analyze_button = ttk.Button(
            auto_actions, text="Analyze clip", command=self._begin_auto_analysis,
        )
        self.analyze_button.pack(side="left", padx=(6, 0))
        for field, label, low, high in (
            ("target", "Target level (1-255)", 1, 255),
            ("strength", "Strength (0-1)", 0, 1),
            ("max_stops", "Maximum correction (stops)", 0, 3),
            ("smoothing_seconds", "Smoothing (seconds)", 0, 5),
        ):
            field_row = ttk.Frame(control_host)
            field_row.pack(fill="x", pady=(1, 1))
            ttk.Label(field_row, text=label).pack(side="left", fill="x", expand=True)
            editor = ttk.Spinbox(
                field_row, from_=low, to=high,
                increment=1 if field == "target" else 0.05,
                textvariable=self.auto_numbers[field], width=9,
                command=self._auto_changed,
            )
            editor.pack(side="right")
            editor.bind("<Return>", lambda _event: self._auto_changed())
            editor.bind("<FocusOut>", lambda _event: self._auto_changed())
        ttk.Label(
            control_host, textvariable=self.auto_info,
            foreground="#666666", wraplength=330,
        ).pack(fill="x", pady=(5, 5))
        ttk.Separator(control_host).pack(fill="x", pady=(5, 10))
        ttk.Label(
            control_host, text="Photography", font=("Segoe UI", 12, "bold")
        ).pack(anchor="w", pady=(0, 6))
        for name, low, high, default, step in CONTROL_SPECS[:9]:
            self._add_slider(control_host, name, low, high, step)

        ttk.Separator(control_host).pack(fill="x", pady=10)
        ttk.Label(
            control_host,
            text="White balance (B / G / R)",
            font=("Segoe UI", 11, "bold"),
        ).pack(anchor="w")
        for name, low, high, default, step in CONTROL_SPECS[9:12]:
            self._add_slider(control_host, name, low, high, step)
        ttk.Label(control_host, text="Calibration target (RRGGBB)").pack(anchor="w")
        target_row = ttk.Frame(control_host)
        target_row.pack(fill="x", pady=(0, 5))
        ttk.Entry(target_row, textvariable=self.calibration_hex, width=11).pack(
            side="left"
        )
        ttk.Button(target_row, text="Sample patch...", command=self._pick_patch).pack(
            side="left", padx=(8, 0)
        )

        ttk.Separator(control_host).pack(fill="x", pady=10)
        ttk.Label(
            control_host,
            text="Selective blue remap",
            font=("Segoe UI", 11, "bold"),
        ).pack(anchor="w")
        ttk.Checkbutton(
            control_host,
            text="Enable remap",
            variable=self.remap_enabled,
            command=self._schedule_preview,
        ).pack(anchor="w")
        ttk.Label(control_host, text="Destination (RRGGBB)").pack(anchor="w")
        remap_entry = ttk.Entry(control_host, textvariable=self.remap_hex, width=12)
        remap_entry.pack(anchor="w")
        remap_entry.bind("<Return>", lambda _: self._schedule_preview())
        remap_entry.bind("<FocusOut>", lambda _: self._schedule_preview())
        for name, low, high, default, step in CONTROL_SPECS[12:]:
            self._add_slider(control_host, name, low, high, step)
        ttk.Button(
            control_host, text="Reset all adjustments", command=self._reset
        ).pack(fill="x", pady=(12, 4))

    def _add_slider(
        self, host: ttk.Frame, name: str, low: float, high: float, step: float
    ) -> None:
        """Keep exact typed numbers independent from rounded slider positions."""
        row = ttk.Frame(host)
        row.pack(fill="x", pady=(2, 2))
        ttk.Label(row, text=name.replace("_", " ").title()).pack(anchor="w")
        input_row = ttk.Frame(row)
        input_row.pack(fill="x")
        slider = tk.Scale(
            input_row,
            from_=low,
            to=high,
            resolution=step,
            orient="horizontal",
            showvalue=False,
            variable=self.values[name],
            command=lambda _=None, key=name: self._slider_changed(key),
            sliderlength=17,
            highlightthickness=0,
        )
        slider.pack(side="left", fill="x", expand=True)
        spinbox = ttk.Spinbox(
            input_row,
            from_=low,
            to=high,
            increment=step,
            textvariable=self.numeric_text[name],
            width=10,
            command=lambda key=name: self._commit_numeric(key),
        )
        spinbox.pack(side="left", padx=(8, 0))
        # Reset only this correction to its neutral value. The global reset
        # in the toolbar also resets remap, target color, and all gains.
        ttk.Button(
            input_row,
            text="Reset",
            width=5,
            command=lambda key=name: self._reset_control(key),
        ).pack(side="left", padx=(5, 0))
        spinbox.bind(
            "<Return>", lambda _event, key=name: self._commit_numeric(key)
        )
        spinbox.bind(
            "<FocusOut>", lambda _event, key=name: self._commit_numeric(key)
        )
        self.controls[name] = slider
        self.spinboxes[name] = spinbox

    def _slider_changed(self, name: str) -> None:
        """Reflect mouse/keyboard slider changes in the exact text field."""
        if self.closing:
            return
        self.numeric_text[name].set(f"{self.values[name].get():.9g}")
        self._schedule_preview()

    def _expand_gain_slider(self, name: str, value: float) -> None:
        """Keep calibrated multiplier values above the normal 8x range usable."""
        if name not in ("blue_gain", "green_gain", "red_gain"):
            return
        slider = self.controls.get(name)
        if slider is not None and value > float(slider.cget("to")):
            new_max = min(255.0, round(max(8.0, value * 1.2), 3))
            slider.configure(to=new_max)
            self.spinboxes[name].configure(to=new_max)

    def _set_number(self, name: str, value: float) -> None:
        """Set calibrated/preset values without discarding fractional precision."""
        self._expand_gain_slider(name, value)
        self.values[name].set(value)
        self.numeric_text[name].set(f"{value:.9g}")

    def _commit_numeric(self, name: str) -> bool:
        """Apply an edited number on Enter, focus loss, or spinbox arrow."""
        if self.closing:
            return False
        try:
            value = validate_number(name, self.numeric_text[name].get())
        except ValueError as exc:
            self.status.set(str(exc))
            return False
        if self.values[name].get() != value:
            self._expand_gain_slider(name, value)
            self.values[name].set(value)
            self._schedule_preview()
        # Normalize only committed text; preserve arbitrary fractional values.
        self.numeric_text[name].set(f"{value:.9g}")
        return True

    def _commit_all_numeric(self) -> None:
        """Validate pending edits before exporting or saving a preset."""
        values = {
            name: validate_number(name, self.numeric_text[name].get())
            for name, *_ in CONTROL_SPECS
        }
        changed = False
        for name, value in values.items():
            if self.values[name].get() != value:
                self._expand_gain_slider(name, value)
                self.values[name].set(value)
                changed = True
            self.numeric_text[name].set(f"{value:.9g}")
        if changed:
            self._schedule_preview()

    def _sync_media_buttons(self) -> None:
        in_image_mode = self.media_kind == "image"
        in_video_mode = self.media_kind == "video"
        self.save_image_button.configure(
            state=("normal" if in_image_mode and not self.exporting else "disabled")
        )
        self.export_button.configure(
            state=("normal" if in_video_mode and not self.exporting else "disabled")
        )
        self.cancel_button.configure(
            state=("normal" if self.exporting else "disabled")
        )
        self.play_button.configure(state=("normal" if in_video_mode else "disabled"))
        self.timeline.configure(state=("normal" if in_video_mode else "disabled"))
        self.analyze_button.configure(
            state=("normal" if in_video_mode and self.auto_enabled.get()
                   else "disabled")
        )
        if self.media_kind == "image":
            self.timeline_text.set("Image mode")
            self.preview_hint.set(
                "Still image mode: full-resolution corrections are used for the "
                "preview and saved image. Type exact values, then press Enter."
            )
        elif self.media_kind == "video":
            self.preview_hint.set(
                "Preview uses full-resolution corrections before scaling for display. "
                "Playback may drop frames when processing is heavy. Audio is preserved on export."
            )
        else:
            self.preview_hint.set(
                "Open a video or image. Video preview playback is silent; exports keep original audio."
            )

    def _get_auto_settings(self) -> AutoExposureSettings:
        """Snapshot dynamic settings without involving background Tk calls."""
        bounds = {
            "target": (1.0, 255.0),
            "strength": (0.0, 1.0),
            "max_stops": (0.0, 3.0),
            "smoothing_seconds": (0.0, 5.0),
        }
        numbers = {}
        for name, (low, high) in bounds.items():
            try:
                number = float(self.auto_numbers[name].get())
            except ValueError as exc:
                raise ValueError(f"Automatic {name} must be numeric.") from exc
            if not math.isfinite(number) or not low <= number <= high:
                raise ValueError(
                    f"Automatic {name} must be between {low:g} and {high:g}."
                )
            numbers[name] = number
        return AutoExposureSettings(
            enabled=self.auto_enabled.get(), roi=tuple(self.auto_roi), **numbers
        )

    def _auto_changed(self) -> None:
        """Apply auto settings; changing the metered area requires a new scan."""
        try:
            auto = self._get_auto_settings()
        except ValueError as exc:
            self.auto_info.set(str(exc))
            return
        self.auto_curve = None
        self.auto_curve_key = None
        self._sync_media_buttons()
        if auto.enabled and self.media_kind == "video":
            if self.auto_levels_key != (self.source_path, auto.roi):
                self._begin_auto_analysis()
        if not auto.enabled:
            self.auto_analysis_cancel.set()
            self.auto_analysis_token += 1
            self.auto_analyzing = False
            self.auto_pending_key = None
            self.auto_info.set("Off. Manual exposure only.")
        self._schedule_preview()

    def _begin_auto_analysis(self) -> None:
        """Scan the video asynchronously to construct a deterministic EV curve."""
        if self.media_kind != "video" or self.source_path is None:
            return
        try:
            auto = self._get_auto_settings()
        except ValueError as exc:
            self.auto_info.set(str(exc))
            return
        if not auto.enabled:
            return
        requested_key = (self.source_path, auto.roi)
        if self.auto_levels_key == requested_key and self.auto_levels is not None:
            self.auto_info.set("Subject lighting analysis ready.")
            self._schedule_preview()
            return
        # Settings such as target and smoothing don't change the raw brightness
        # readings, so don't restart the scan while one is already running.
        if (self.auto_analyzing and self.auto_pending_key == requested_key
                and not self.auto_analysis_cancel.is_set()):
            return
        # Obsolete analyses are canceled; only the newest token can update UI.
        self.auto_analysis_cancel.set()
        self.auto_analysis_cancel = threading.Event()
        self.auto_analysis_token += 1
        token = self.auto_analysis_token
        source = self.source_path
        roi = auto.roi
        cancel = self.auto_analysis_cancel
        self.auto_analyzing = True
        self.auto_pending_key = requested_key
        self.auto_info.set("Analyzing subject brightness across the clip...")

        def worker() -> None:
            try:
                levels = analyze_video_levels(
                    source, roi, cancel,
                    lambda done, total: self.events.put(
                        ("auto_scan", token, done, total)
                    ),
                )
                self.events.put(("auto_done", token, source, roi, levels))
            except InterruptedError:
                self.events.put(("auto_stopped", token))
            except Exception as exc:
                self.events.put(("auto_error", token, str(exc)))

        self.auto_analysis_thread = threading.Thread(
            target=worker, name="Subject exposure analysis", daemon=True
        )
        self.auto_analysis_thread.start()

    def _auto_exposure_for_frame(
        self, frame, index: int, auto: AutoExposureSettings
    ) -> float:
        """Use analyzed timeline when available; otherwise instant metering."""
        if not auto.enabled:
            return 0.0
        key = (self.source_path, auto.roi)
        if (self.media_kind == "video" and self.auto_levels is not None
                and self.auto_levels_key == key):
            curve_key = (key, self.fps, auto)
            if self.auto_curve is None or self.auto_curve_key != curve_key:
                self.auto_curve = calculate_exposure_curve(
                    self.auto_levels, self.fps, auto
                )
                self.auto_curve_key = curve_key
            return self.auto_curve[min(max(index, 0), len(self.auto_curve) - 1)]
        # Temporary instant estimate until the full video scan completes.
        return calculate_exposure_curve(
            [measure_subject_level(frame, auto.roi)], 1.0, auto
        )[0]

    def _select_subject_area(self) -> None:
        """Select a rectangular ROI on the uncorrected frame or still image."""
        if self.source_frame is None:
            messagebox.showinfo("Subject exposure", "Open a video or image first.")
            return
        self._pause()
        frame = self.source_frame.copy()
        height, width = frame.shape[:2]
        scale = min(900 / width, 650 / height, 1.0)
        shown = cv2.resize(
            frame,
            (max(1, round(width * scale)), max(1, round(height * scale))),
            interpolation=cv2.INTER_AREA,
        )
        popup = tk.Toplevel(self.root)
        popup.title("Select the subject to meter")
        ttk.Label(
            popup,
            text="Drag over the subject. Background outside this area is ignored "
                 "when calculating exposure.",
        ).pack(padx=10, pady=8)
        canvas = tk.Canvas(
            popup, width=shown.shape[1], height=shown.shape[0],
            highlightthickness=0, cursor="crosshair",
        )
        canvas.pack(padx=10, pady=10)
        photo = self._to_photo(shown)
        canvas.image = photo
        canvas.create_image(0, 0, anchor="nw", image=photo)
        box = {"origin": None, "rect": None}

        def press(event) -> None:
            box["origin"] = (event.x, event.y)

        def drag(event) -> None:
            if box["origin"] is None:
                return
            if box["rect"] is not None:
                canvas.delete(box["rect"])
            x, y = box["origin"]
            box["rect"] = canvas.create_rectangle(
                x, y, event.x, event.y, outline="cyan", width=2
            )

        def release(event) -> None:
            if box["origin"] is None:
                return
            x1, y1 = box["origin"]
            x2, y2 = event.x, event.y
            x1, x2 = sorted((max(0, min(shown.shape[1], x1)),
                              max(0, min(shown.shape[1], x2))))
            y1, y2 = sorted((max(0, min(shown.shape[0], y1)),
                              max(0, min(shown.shape[0], y2))))
            if x2 - x1 < 3 or y2 - y1 < 3:
                messagebox.showinfo(
                    "Subject exposure", "Draw a larger region.", parent=popup
                )
                return
            self.auto_roi = (
                x1 / shown.shape[1], y1 / shown.shape[0],
                x2 / shown.shape[1], y2 / shown.shape[0],
            )
            self.auto_levels = None
            self.auto_levels_key = None
            self.auto_curve = None
            self.auto_curve_key = None
            self.auto_info.set("Subject region selected.")
            popup.destroy()
            self._auto_changed()

        canvas.bind("<ButtonPress-1>", press)
        canvas.bind("<B1-Motion>", drag)
        canvas.bind("<ButtonRelease-1>", release)

    def _get_settings(self, *, commit_edits: bool = True) -> ColorSettings:
        if commit_edits:
            self._commit_all_numeric()
        low = int(round(self.values["hue_low"].get()))
        high = int(round(self.values["hue_high"].get()))
        if low > high:
            raise ValueError("Blue hue lower bound cannot exceed upper bound.")
        remap = self.remap_hex.get().strip() if self.remap_enabled.get() else None
        if remap:
            hex_to_bgr(remap)
        return ColorSettings(
            offset=(
                self.values["blue_gain"].get(),
                self.values["green_gain"].get(),
                self.values["red_gain"].get(),
            ),
            remap_blue=remap,
            blue_range=(low, high),
            **{name: self.values[name].get() for name, *_ in CONTROL_SPECS[:9]},
        )

    def _set_source_frame(self, frame, *, kind: str, path: Path, status_text: str) -> None:
        self.auto_analysis_cancel.set()
        self.auto_levels = None
        self.auto_levels_key = None
        self.auto_curve = None
        self.auto_curve_key = None
        self.source_path = path
        self.media_kind = kind
        self.source_frame = frame
        self.region_box = None
        self.current_frame = 0
        if kind == "image":
            if self.capture is not None:
                self.capture.release()
                self.capture = None
            self.frame_count = 1
            self.fps = 1.0
            self._pause()
            self.timeline_var.set(0)
        self.status.set(status_text)
        self._sync_media_buttons()
        self._schedule_preview()

    def _open_video(self) -> None:
        path = filedialog.askopenfilename(
            title="Select video",
            filetypes=[
                ("Video files", "*.mp4 *.mov *.avi *.mkv *.webm *.m4v"),
                ("All files", "*.*"),
            ],
        )
        if not path:
            return
        self._pause()
        candidate = cv2.VideoCapture(path)
        if not candidate.isOpened():
            messagebox.showerror("Open video", f"Cannot open {path}")
            return
        if self.capture is not None:
            self.capture.release()
        self.capture = candidate
        source_path = Path(path)
        self.frame_count = max(0, int(candidate.get(cv2.CAP_PROP_FRAME_COUNT)))
        self.fps = candidate.get(cv2.CAP_PROP_FPS) or 30.0
        self.media_kind = "video"
        self.region_box = None
        self.auto_analysis_cancel.set()
        self.auto_levels = None
        self.auto_levels_key = None
        self.auto_curve = None
        self.auto_curve_key = None
        self.timeline.configure(to=max(1, self.frame_count - 1))
        self.source_path = source_path
        self._read_at(0)
        self.status.set(
            f"Opened video {source_path.name} | "
            f"{int(candidate.get(cv2.CAP_PROP_FRAME_WIDTH))} x "
            f"{int(candidate.get(cv2.CAP_PROP_FRAME_HEIGHT))} | "
            f"{self.fps:.3f} FPS"
        )
        self.source_path = source_path
        self._sync_media_buttons()
        if self.auto_enabled.get():
            self._begin_auto_analysis()

    def _open_image(self) -> None:
        path = filedialog.askopenfilename(
            title="Select image",
            filetypes=[
                ("Image files", "*.png *.jpg *.jpeg *.bmp *.tif *.tiff *.webp"),
                ("All files", "*.*"),
            ],
        )
        if not path:
            return
        image = cv2.imread(path, cv2.IMREAD_UNCHANGED)
        if image is None:
            messagebox.showerror("Open image", f"Cannot open {path}")
            return
        h, w = image.shape[:2]
        self._set_source_frame(
            image,
            kind="image",
            path=Path(path),
            status_text=f"Opened image {Path(path).name} | {w} x {h}",
        )

    def _read_at(self, index: int) -> None:
        if self.capture is None:
            return
        self.capture.set(cv2.CAP_PROP_POS_FRAMES, index)
        ok, frame = self.capture.read()
        if not ok:
            self.status.set(f"Unable to decode frame {index}.")
            return
        if self.current_frame != index:
            self.region_box = None
        self.current_frame = index
        self.source_frame = frame
        self.setting_timeline = True
        self.timeline_var.set(index)
        self.setting_timeline = False
        self._update_time()
        self._schedule_preview()

    def _timeline_changed(self, position: str) -> None:
        if self.setting_timeline or self.capture is None or self.media_kind != "video":
            return
        if abs(float(position) - self.current_frame) < 0.5:
            return
        self._pause()
        if self.seek_job:
            self.root.after_cancel(self.seek_job)
        index = int(round(float(position)))
        self.seek_job = self.root.after(65, lambda: self._read_at(index))

    def _update_time(self) -> None:
        if self.media_kind != "video":
            self.timeline_text.set("Image mode")
            return
        current = self.current_frame / self.fps
        total = self.frame_count / self.fps if self.frame_count else current

        def fmt(seconds: float) -> str:
            return f"{int(seconds // 60):02d}:{int(seconds % 60):02d}"

        self.timeline_text.set(f"{fmt(current)} / {fmt(total)}")

    def _schedule_preview(self) -> None:
        """Queue the latest accurate frame without blocking the Tk UI."""
        if self.source_frame is None or self.closing:
            return
        self.preview_generation += 1
        if self.preview_job:
            self.root.after_cancel(self.preview_job)
        self.preview_job = self.root.after(35, self._render_preview)

    def _to_photo(self, frame) -> ImageTk.PhotoImage:
        if frame.ndim == 2:
            converted = cv2.cvtColor(frame, cv2.COLOR_GRAY2RGB)
        elif frame.ndim == 3 and frame.shape[2] == 4:
            converted = cv2.cvtColor(frame, cv2.COLOR_BGRA2RGBA)
        else:
            converted = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        return ImageTk.PhotoImage(Image.fromarray(converted))

    def _hover_preview_pixel(self, event, original: bool) -> None:
        """Map a scaled, centered Tk preview image to full-size X/Y pixels."""
        photo = self.original_photo if original else self.adjusted_photo
        label = self.original_label if original else self.adjusted_label
        if self.source_frame is None or photo is None:
            self.pixel_position.set("X: —  Y: —")
            return
        photo_width = photo.width()
        photo_height = photo.height()
        image_x = event.x - (label.winfo_width() - photo_width) / 2
        image_y = event.y - (label.winfo_height() - photo_height) / 2
        if not (0 <= image_x < photo_width and 0 <= image_y < photo_height):
            self.pixel_position.set("X: —  Y: —")
            return
        height, width = self.source_frame.shape[:2]
        x = min(width - 1, int(image_x * width / photo_width))
        y = min(height - 1, int(image_y * height / photo_height))
        self.pixel_position.set(f"X: {x:,}  Y: {y:,}")

    def _store_region_box(
        self, box: tuple[int, int, int, int] | None
    ) -> None:
        """Store the latest full-resolution annotation for later workflows."""
        self.region_box = box
        if box is None:
            self.status.set("Region annotation cleared.")
        else:
            x, y, width, height = box
            self.status.set(
                f"Region selected: X={x}, Y={y}, W={width}, H={height}"
            )
        self._schedule_preview()

    def _inspect_region(self) -> None:
        """Open a zoomable image inspector with a draggable ROI rectangle."""
        if self.source_frame is None:
            messagebox.showinfo("Inspect region", "Open a photo or video first.")
            return
        self._pause()
        filename = (
            self.source_path.name if self.source_path is not None else "image"
        )
        RegionInspector(
            self.root,
            self.source_frame,
            filename,
            on_box=self._store_region_box,
            initial_box=self.region_box,
            frame_index=self.current_frame if self.media_kind == "video" else None,
        )

    def _render_preview(self) -> None:
        """Snapshot the frame and corrections for the background preview."""
        self.preview_job = None
        if self.source_frame is None or self.closing:
            return
        try:
            # An uncommitted text edit is not yet part of the slider settings.
            settings = self._get_settings(commit_edits=False)
            auto = self._get_auto_settings()
            if auto.enabled:
                extra = self._auto_exposure_for_frame(
                    self.source_frame, self.current_frame, auto
                )
                settings = subject_adjusted_settings(settings, extra)
                ready = (
                    self.media_kind != "video"
                    or self.auto_levels_key == (self.source_path, auto.roi)
                )
                suffix = "" if ready else " (temporary; analysis pending)"
                self.auto_info.set(
                    f"Subject correction: {extra:+.2f} stops{suffix}"
                )
        except (ValueError, tk.TclError) as exc:
            self.status.set(f"Preview settings error: {exc}")
            return
        self.preview_pending = (
            self.preview_generation, self.source_frame, settings
        )
        self._dispatch_preview()

    def _dispatch_preview(self) -> None:
        """At most one calculation runs, with only the latest frame pending."""
        if self.preview_busy or self.preview_pending is None or self.closing:
            return
        generation, frame, settings = self.preview_pending
        self.preview_pending = None
        self.preview_busy = True

        def worker() -> None:
            try:
                original, corrected = prepare_preview_frame(
                    frame, settings, self.PREVIEW_WIDTH, self.PREVIEW_HEIGHT
                )
                self.events.put(("preview", generation, original, corrected))
            except Exception as exc:
                self.events.put(("preview_error", generation, str(exc)))

        threading.Thread(
            target=worker, name="Accurate preview worker", daemon=True
        ).start()

    def _display_preview(self, original, corrected) -> None:
        """Draw analysis overlays only on previews, never on exported images."""
        shown_original = original.copy()
        shown_corrected = corrected.copy()
        h, w = shown_original.shape[:2]
        if self.auto_enabled.get():
            x1, y1, x2, y2 = self.auto_roi
            cv2.rectangle(
                shown_original, (round(x1 * w), round(y1 * h)),
                (round(x2 * w), round(y2 * h)), (255, 255, 0), 2,
            )

        if self.region_box is not None and self.source_frame is not None:
            full_height, full_width = self.source_frame.shape[:2]
            x, y, box_width, box_height = self.region_box
            start = (round(x * w / full_width), round(y * h / full_height))
            end = (
                round((x + box_width) * w / full_width),
                round((y + box_height) * h / full_height),
            )
            for preview in (shown_original, shown_corrected):
                cv2.rectangle(preview, start, end, (165, 235, 134), 2)

        self.original_photo = self._to_photo(shown_original)
        self.adjusted_photo = self._to_photo(shown_corrected)
        self.original_label.configure(image=self.original_photo, text="")
        self.adjusted_label.configure(image=self.adjusted_photo, text="")

    def _toggle_play(self) -> None:
        if self.capture is None or self.media_kind != "video":
            return
        if self.playing:
            self._pause()
            return
        if self.frame_count and self.current_frame >= self.frame_count - 1:
            self._read_at(0)
        self.playing = True
        self.play_button.configure(text="Pause")
        self._play_next()

    def _play_next(self) -> None:
        self.play_job = None
        if not self.playing or self.capture is None or self.media_kind != "video":
            return
        ok, frame = self.capture.read()
        if not ok:
            self._pause()
            return
        self.current_frame += 1
        self.source_frame = frame
        self.setting_timeline = True
        self.timeline_var.set(self.current_frame)
        self.setting_timeline = False
        self._update_time()
        self._schedule_preview()
        self.play_job = self.root.after(
            max(1, round(1000.0 / self.fps)), self._play_next
        )

    def _pause(self) -> None:
        self.playing = False
        self.play_button.configure(text="Play")
        if self.play_job:
            self.root.after_cancel(self.play_job)
            self.play_job = None

    def _pick_patch(self) -> None:
        if self.source_frame is None:
            messagebox.showinfo("Calibration", "Open a video or image first.")
            return
        self._pause()
        try:
            hex_to_bgr(self.calibration_hex.get().strip())
        except ValueError as exc:
            messagebox.showerror("Calibration", str(exc))
            return

        image = self.source_frame.copy()
        height, width = image.shape[:2]
        scale = min(900 / width, 650 / height, 1.0)
        shown = cv2.resize(
            image,
            (max(1, round(width * scale)), max(1, round(height * scale))),
            interpolation=cv2.INTER_AREA,
        )
        popup = tk.Toplevel(self.root)
        popup.title("Drag over a neutral reference area")
        ttk.Label(
            popup,
            text="Drag a rectangle over a neutral-colored patch; "
            "release to calculate fixed RGB multipliers.",
        ).pack(padx=10, pady=8)
        canvas = tk.Canvas(
            popup,
            width=shown.shape[1],
            height=shown.shape[0],
            highlightthickness=0,
            cursor="crosshair",
        )
        canvas.pack(padx=10, pady=(0, 10))
        photo = self._to_photo(shown)
        canvas.image = photo
        canvas.create_image(0, 0, anchor="nw", image=photo)
        selection = {"start": None, "rect": None}

        def press(event) -> None:
            selection["start"] = (event.x, event.y)
            if selection["rect"]:
                canvas.delete(selection["rect"])

        def drag(event) -> None:
            start = selection["start"]
            if start is None:
                return
            if selection["rect"]:
                canvas.delete(selection["rect"])
            selection["rect"] = canvas.create_rectangle(
                start[0], start[1], event.x, event.y, outline="yellow", width=2
            )

        def release(event) -> None:
            start = selection["start"]
            if start is None:
                return
            x1, y1 = start
            x2, y2 = event.x, event.y
            x1 = max(0, min(shown.shape[1], x1))
            x2 = max(0, min(shown.shape[1], x2))
            y1 = max(0, min(shown.shape[0], y1))
            y2 = max(0, min(shown.shape[0], y2))
            coords = (
                round(x1 / scale),
                round(y1 / scale),
                round(x2 / scale),
                round(y2 / scale),
            )
            try:
                gains = measure_balance_offset(
                    image, coords, self.calibration_hex.get().strip()
                )
            except ValueError as exc:
                messagebox.showerror("Calibration", str(exc), parent=popup)
                return
            for key, gain in zip(("blue_gain", "green_gain", "red_gain"), gains):
                slider = self.controls[key]
                spinbox = self.spinboxes[key]
                if gain > float(slider.cget("to")):
                    new_max = min(255.0, round(gain * 1.2, 3))
                    slider.configure(to=new_max)
                    spinbox.configure(to=new_max)
                self._set_number(key, gain)
            self.status.set(
                "Calibrated fixed B/G/R gains: "
                + ", ".join(f"{value:.3f}" for value in gains)
            )
            self._schedule_preview()
            popup.destroy()

        canvas.bind("<ButtonPress-1>", press)
        canvas.bind("<B1-Motion>", drag)
        canvas.bind("<ButtonRelease-1>", release)

    def _reset_control(self, name: str) -> None:
        """Reset one adjustment without disturbing the other controls."""
        spec = next((item for item in CONTROL_SPECS if item[0] == name), None)
        if spec is None:
            raise KeyError(f"Unknown correction: {name}")
        _, _, high, default, _ = spec
        if name in ("blue_gain", "green_gain", "red_gain"):
            # Calibration can temporarily expand gain sliders beyond 8x.
            self.controls[name].configure(to=high)
            self.spinboxes[name].configure(to=high)
        self._set_number(name, default)
        self._schedule_preview()
        self.status.set(f"Reset {name.replace('_', ' ')} to {default:g}.")

    def _reset(self) -> None:
        """Restore all neutral values, including remap and calibration."""
        for name, _, high, default, _ in CONTROL_SPECS:
            if name in ("blue_gain", "green_gain", "red_gain"):
                self.controls[name].configure(to=high)
                self.spinboxes[name].configure(to=high)
            self._set_number(name, default)
        self.remap_enabled.set(False)
        self.remap_hex.set("FF8800")
        self.calibration_hex.set("FFFFFF")
        self.auto_enabled.set(False)
        self.auto_roi = AutoExposureSettings().roi
        for name, value in (
            ("target", "135"), ("strength", "1"),
            ("max_stops", "1.5"), ("smoothing_seconds", "0.65"),
        ):
            self.auto_numbers[name].set(value)
        self.auto_analysis_cancel.set()
        self.auto_levels = None
        self.auto_levels_key = None
        self.auto_curve = None
        self.auto_curve_key = None
        self._sync_media_buttons()
        self.auto_info.set("Off. Manual exposure only.")
        self._schedule_preview()
        self.status.set("All adjustments reset to neutral defaults.")

    def _save_preset(self) -> None:
        try:
            settings = self._get_settings()
            self._get_auto_settings()
        except ValueError as exc:
            messagebox.showerror("Preset", str(exc))
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".json", filetypes=[("JSON preset", "*.json")]
        )
        if not path:
            return
        Path(path).write_text(
            json.dumps(
                {
                    "settings": asdict(settings),
                    "calibration_hex": self.calibration_hex.get(),
                    "remap_hex": self.remap_hex.get(),
                    "auto_exposure": asdict(self._get_auto_settings()),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        self.status.set(f"Saved preset: {path}")

    def _load_preset(self) -> None:
        path = filedialog.askopenfilename(
            filetypes=[("JSON preset", "*.json"), ("All files", "*.*")]
        )
        if not path:
            return
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
            data = payload["settings"]
            settings = ColorSettings(**data)
            if settings.remap_blue:
                hex_to_bgr(settings.remap_blue)
            for name, *_ in CONTROL_SPECS[:9]:
                self._set_number(name, getattr(settings, name))
            for name, gain in zip(
                ("blue_gain", "green_gain", "red_gain"),
                settings.offset or (1.0, 1.0, 1.0),
            ):
                slider = self.controls[name]
                spinbox = self.spinboxes[name]
                if gain > float(slider.cget("to")):
                    new_max = min(255.0, round(gain * 1.2, 3))
                    slider.configure(to=new_max)
                    spinbox.configure(to=new_max)
                self._set_number(name, gain)
            self._set_number("hue_low", settings.blue_range[0])
            self._set_number("hue_high", settings.blue_range[1])
            self.remap_enabled.set(bool(settings.remap_blue))
            self.remap_hex.set(settings.remap_blue or payload.get("remap_hex", "FF8800"))
            self.calibration_hex.set(payload.get("calibration_hex", "FFFFFF"))
            auto = AutoExposureSettings(**payload.get("auto_exposure", {}))
            self.auto_enabled.set(auto.enabled)
            self.auto_roi = tuple(auto.roi)
            for name in self.auto_numbers:
                self.auto_numbers[name].set(f"{getattr(auto, name):g}")
            self.auto_levels = None
            self.auto_levels_key = None
            self.auto_curve = None
            self.auto_curve_key = None
            self._auto_changed()
            self._schedule_preview()
            self.status.set(f"Loaded preset: {path}")
        except (OSError, KeyError, TypeError, ValueError) as exc:
            messagebox.showerror("Load preset", f"Invalid preset: {exc}")

    def _save_image(self) -> None:
        if self.media_kind != "image" or self.source_path is None:
            messagebox.showinfo("Save image", "Open an image first.")
            return
        try:
            settings = self._get_settings()
            auto = self._get_auto_settings()
        except ValueError as exc:
            messagebox.showerror("Save image", str(exc))
            return
        suggested = f"{self.source_path.stem}_graded{self.source_path.suffix}"
        path = filedialog.asksaveasfilename(
            title="Save corrected image",
            initialfile=suggested,
            defaultextension=self.source_path.suffix or ".png",
            filetypes=[
                ("PNG image", "*.png"),
                ("JPEG image", "*.jpg *.jpeg"),
                ("TIFF image", "*.tif *.tiff"),
                ("Bitmap image", "*.bmp"),
                ("WebP image", "*.webp"),
                ("All files", "*.*"),
            ],
        )
        if not path:
            return
        destination = Path(path)
        if destination.resolve() == self.source_path.resolve():
            messagebox.showerror("Save image", "Choose a different output filename.")
            return
        try:
            save_corrected_image(self.source_path, destination, settings, auto)
        except Exception as exc:
            messagebox.showerror("Save image", str(exc))
            return
        self.status.set(f"Saved corrected image: {destination}")
        messagebox.showinfo("Save image", str(destination))

    def _start_export(self) -> None:
        if self.exporting or self.source_path is None or self.media_kind != "video":
            if self.media_kind != "video":
                messagebox.showinfo("Export", "Open a video first.")
            return
        try:
            settings = self._get_settings()
            auto = self._get_auto_settings()
        except ValueError as exc:
            messagebox.showerror("Export", str(exc))
            return
        if not shutil.which("ffmpeg"):
            messagebox.showerror(
                "FFmpeg required",
                "Install FFmpeg and add it to your system PATH to export MP4.",
            )
            return
        suggested = f"{self.source_path.stem}_graded.mp4"
        path = filedialog.asksaveasfilename(
            title="Export corrected MP4",
            initialfile=suggested,
            defaultextension=".mp4",
            filetypes=[("MP4 video", "*.mp4")],
        )
        if not path:
            return
        destination = Path(path)
        source_for_export = self.source_path
        if destination.resolve() == source_for_export.resolve():
            messagebox.showerror("Export", "Choose a different output filename.")
            return

        self._pause()
        self.cancel_event.clear()
        # Reuse exactly the measurements used for the preview if available.
        readings = (
            list(self.auto_levels)
            if auto.enabled and self.auto_levels is not None
            and self.auto_levels_key == (source_for_export, auto.roi)
            else None
        )
        self.exporting = True
        self.export_started_at = time.monotonic()
        self.export_last_update_at = self.export_started_at
        self.export_stage = "Starting export"
        self.export_progress = ""
        self.progress_bar.configure(value=0)
        self.status.set("Starting export. The interface remains available...")
        self._sync_media_buttons()

        def worker() -> None:
            try:
                export_video(
                    source_for_export,
                    destination,
                    settings,
                    self.cancel_event,
                    lambda done, total: self.events.put(("progress", done, total)),
                    lambda stage: self.events.put(("stage", stage)),
                    auto=auto, precomputed_levels=readings,
                )
                self.events.put(("success", str(destination)))
            except InterruptedError:
                self.events.put(("canceled",))
            except Exception as exc:
                self.events.put(("error", str(exc)))

        self.export_thread = threading.Thread(target=worker, daemon=True)
        self.export_thread.start()

    def _cancel_export(self) -> None:
        if not self.exporting:
            return
        self.cancel_event.set()
        self.cancel_button.configure(state="disabled")
        self.export_stage = "Canceling export"
        self.status.set("Canceling export and cleaning temporary files...")

    def _poll_events(self) -> None:
        try:
            for _ in range(100):
                event = self.events.get_nowait()
                kind = event[0]
                if kind in ("progress", "stage", "success", "canceled", "error"):
                    self.export_last_update_at = time.monotonic()
                if kind == "preview" or kind == "preview_error":
                    self.preview_busy = False
                    # While playing, full-resolution processing may take longer
                    # than a frame interval. Display the latest completed frame
                    # instead of starving video playback indefinitely.
                    current = event[1] == self.preview_generation
                    if current or (self.playing and kind == "preview"):
                        if kind == "preview":
                            self._display_preview(event[2], event[3])
                        else:
                            self.status.set(f"Preview settings error: {event[2]}")
                    self._dispatch_preview()
                elif kind == "progress":
                    _, count, total = event
                    if total > 0:
                        if self.export_stage.startswith("Analyzing"):
                            value = min(15, 15 * count / total)
                        else:
                            value = min(90, 15 + 75 * count / total)
                        self.progress_bar.configure(value=value)
                    self.export_progress = (
                        f"Frame {count:,} / {total:,}" if total > 0 else f"Frame {count:,}"
                    )
                elif kind == "stage":
                    self.export_stage = event[1]
                    self.export_progress = ""
                    if self.export_stage.startswith("Muxing"):
                        self.progress_bar.configure(value=90)
                elif kind == "auto_scan":
                    _, token, count, total = event
                    if token == self.auto_analysis_token:
                        self.auto_info.set(
                            f"Analyzing subject: frame {count:,} / {total:,}"
                        )
                elif kind == "auto_done":
                    _, token, source, roi, levels = event
                    if token == self.auto_analysis_token:
                        self.auto_levels = levels
                        self.auto_levels_key = (source, roi)
                        self.auto_curve = None
                        self.auto_curve_key = None
                        self.auto_analyzing = False
                        self.auto_pending_key = None
                        self.auto_info.set("Subject lighting scan complete.")
                        self._schedule_preview()
                elif kind == "auto_error":
                    _, token, error = event
                    if token == self.auto_analysis_token:
                        self.auto_analyzing = False
                        self.auto_pending_key = None
                        self.auto_info.set(f"Lighting scan failed: {error}")
                elif kind == "auto_stopped":
                    _, token = event
                    if token == self.auto_analysis_token:
                        self.auto_analyzing = False
                        self.auto_pending_key = None
                else:
                    self.exporting = False
                    self._sync_media_buttons()
                    if kind == "success":
                        self.progress_bar.configure(value=100)
                        self.status.set(f"Exported: {event[1]}")
                        messagebox.showinfo("Export complete", event[1])
                    elif kind == "canceled":
                        self.progress_bar.configure(value=0)
                        self.status.set("Export canceled. Temporary files removed.")
                    elif kind == "error":
                        self.status.set("Export failed. See the error message.")
                        messagebox.showerror("Export failed", event[1])
        except queue.Empty:
            pass

        if self.exporting:
            elapsed = int(time.monotonic() - self.export_started_at)
            details = f" | {self.export_progress}" if self.export_progress else ""
            self.status.set(
                f"{self.export_stage}{details} | elapsed {elapsed}s"
                + (
                    " | still working..."
                    if time.monotonic() - self.export_last_update_at >= 3
                    else ""
                )
            )
        self.root.after(150, self._poll_events)

    def _close(self) -> None:
        if self.exporting:
            if not messagebox.askyesno(
                "Export in progress", "Cancel the export and close?"
            ):
                return
            self._cancel_export()
        self.closing = True
        self._pause()
        self.auto_analysis_cancel.set()
        if self.capture is not None:
            self.capture.release()
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    VideoColorApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()