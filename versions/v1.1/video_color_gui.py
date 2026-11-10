"""Video color correction GUI using Tkinter, OpenCV, and FFmpeg.

Run: python video_color_gui.py

The GUI previews reduced-size frames for responsiveness and uses full-size
frames only for export. The existing HueSatVal3.py remains unchanged.
"""

from __future__ import annotations

import json
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

from color_pipeline import (
    ColorSettings,
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

    # No shell=True: filenames and paths are passed directly to FFmpeg.
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
            pass  # Cancellation may have broken the pipe.
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


def export_video(
    source: Path,
    destination: Path,
    settings: ColorSettings,
    stop_event: threading.Event,
    report: Callable[[int, int], None],
    report_stage: Callable[[str], None] | None = None,
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

        with tempfile.TemporaryDirectory(
            prefix=".huesatval_export_", dir=str(destination.parent)
        ) as workspace:
            workdir = Path(workspace)
            silent_video = workdir / "corrected_video.mp4"
            finished_video = workdir / "video_with_audio.mp4"

            # A fast H.264 preset keeps CPU load reasonable while making the
            # same pixel-level corrections as the interactive preview.
            encode_command = [
                ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                "-f", "rawvideo", "-pix_fmt", "bgr24",
                "-s", f"{width}x{height}", "-r", f"{fps:.10f}",
                "-i", "pipe:0", "-an",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                "-threads", "4",
                "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2",
                "-pix_fmt", "yuv420p",
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
                        corrected = process_frame(frame, settings)
                        if process.poll() is not None:
                            if stop_event.is_set():
                                raise InterruptedError("Export canceled")
                            raise _ffmpeg_error(
                                errors, "Video encoder stopped unexpectedly",
                                process.returncode,
                            )
                        if process.stdin is None:
                            raise RuntimeError("Encoder input pipe is unavailable.")
                        # This call can block while FFmpeg encodes. The watcher
                        # above interrupts it if Cancel export is clicked.
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

            # Encoding is complete. Release input capture before asking FFmpeg
            # to read the source audio, avoiding competing access to the file.
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
            # Staged in destination.parent for same-volume atomic replacement.
            os.replace(finished_video, destination)
            stage("Export complete")
    finally:
        capture.release()


class VideoColorApp:
    """Preview and export video using the shared still-image corrections."""

    PREVIEW_WIDTH = 535
    PREVIEW_HEIGHT = 355

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("HueSatVal Video Lab")
        self.root.geometry("1450x870")
        self.root.minsize(1100, 650)

        self.source_path: Path | None = None
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
        self.remap_enabled = tk.BooleanVar(value=False)
        self.remap_hex = tk.StringVar(value="FF8800")
        self.calibration_hex = tk.StringVar(value="FFFFFF")
        self.status = tk.StringVar(value="Load a video to begin.")
        self.timeline_text = tk.StringVar(value="00:00 / 00:00")
        self.timeline_var = tk.DoubleVar(value=0)

        self._build_ui()
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
        ttk.Button(header, text="Save preset", command=self._save_preset).pack(
            side="left", padx=(0, 6)
        )
        ttk.Button(header, text="Load preset", command=self._load_preset).pack(
            side="left", padx=(0, 6)
        )
        self.export_button = ttk.Button(
            header, text="Export MP4", command=self._start_export
        )
        self.export_button.pack(side="left", padx=(0, 6))
        self.cancel_button = ttk.Button(
            header, text="Cancel export", command=self._cancel_export,
            state="disabled",
        )
        self.cancel_button.pack(side="left")

        main = ttk.Frame(outer)
        main.pack(fill="both", expand=True)
        preview_area = ttk.Frame(main)
        preview_area.pack(side="left", fill="both", expand=True)
        controls_area = ttk.Frame(main, width=325)
        controls_area.pack(side="right", fill="y", padx=(14, 0))
        controls_area.pack_propagate(False)

        compare = ttk.Frame(preview_area)
        compare.pack(fill="both", expand=True)
        left = ttk.LabelFrame(compare, text="Original", padding=5)
        right = ttk.LabelFrame(compare, text="Corrected preview", padding=5)
        left.pack(side="left", fill="both", expand=True, padx=(0, 4))
        right.pack(side="left", fill="both", expand=True, padx=(4, 0))
        self.original_label = ttk.Label(left, text="No video loaded", anchor="center")
        self.adjusted_label = ttk.Label(right, text="No video loaded", anchor="center")
        self.original_label.pack(fill="both", expand=True)
        self.adjusted_label.pack(fill="both", expand=True)
        self.original_photo = None
        self.adjusted_photo = None

        player = ttk.Frame(preview_area)
        player.pack(fill="x", pady=(10, 0))
        self.play_button = ttk.Button(player, text="Play", command=self._toggle_play)
        self.play_button.pack(side="left")
        ttk.Label(player, textvariable=self.timeline_text, width=16).pack(
            side="right"
        )
        self.timeline = ttk.Scale(
            player, from_=0, to=1, variable=self.timeline_var,
            command=self._timeline_changed,
        )
        self.timeline.pack(side="left", fill="x", expand=True, padx=8)
        ttk.Label(
            preview_area,
            text="Preview playback is silent and may slow down with heavy filters. "
                 "Exports use original audio.",
            foreground="#666666",
        ).pack(anchor="w", pady=(8, 0))

        self.progress_bar = ttk.Progressbar(outer, mode="determinate")
        self.progress_bar.pack(fill="x", pady=(10, 2))
        ttk.Label(outer, textvariable=self.status, anchor="w").pack(fill="x")

        # Scrollable sidebar containing every control from the source script.
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
            control_host, text="Photography", font=("Segoe UI", 12, "bold")
        ).pack(anchor="w", pady=(0, 6))
        for name, low, high, default, step in CONTROL_SPECS[:9]:
            self._add_slider(control_host, name, low, high, step)

        ttk.Separator(control_host).pack(fill="x", pady=10)
        ttk.Label(
            control_host, text="White balance (B / G / R)",
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
        ttk.Button(
            target_row, text="Sample patch...", command=self._pick_patch
        ).pack(side="left", padx=(8, 0))

        ttk.Separator(control_host).pack(fill="x", pady=10)
        ttk.Label(
            control_host, text="Selective blue remap", font=("Segoe UI", 11, "bold")
        ).pack(anchor="w")
        ttk.Checkbutton(
            control_host, text="Enable remap", variable=self.remap_enabled,
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
        row = ttk.Frame(host)
        row.pack(fill="x", pady=(1, 1))
        ttk.Label(row, text=name.replace("_", " ").title()).pack(anchor="w")
        slider = tk.Scale(
            row, from_=low, to=high, resolution=step,
            orient="horizontal", showvalue=True, variable=self.values[name],
            command=lambda _: self._schedule_preview(),
            sliderlength=17, highlightthickness=0,
        )
        slider.pack(fill="x")
        self.controls[name] = slider

    def _get_settings(self) -> ColorSettings:
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
            **{
                name: self.values[name].get()
                for name, *_ in CONTROL_SPECS[:9]
            },
        )

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
        self.source_path = Path(path)
        self.frame_count = max(0, int(candidate.get(cv2.CAP_PROP_FRAME_COUNT)))
        self.fps = candidate.get(cv2.CAP_PROP_FPS) or 30.0
        self.timeline.configure(to=max(1, self.frame_count - 1))
        self._read_at(0)
        self.status.set(
            f"Opened {self.source_path.name} | "
            f"{int(candidate.get(cv2.CAP_PROP_FRAME_WIDTH))} x "
            f"{int(candidate.get(cv2.CAP_PROP_FRAME_HEIGHT))} | "
            f"{self.fps:.3f} FPS"
        )

    def _read_at(self, index: int) -> None:
        if self.capture is None:
            return
        self.capture.set(cv2.CAP_PROP_POS_FRAMES, index)
        ok, frame = self.capture.read()
        if not ok:
            self.status.set(f"Unable to decode frame {index}.")
            return
        self.current_frame = index
        self.source_frame = frame
        self.setting_timeline = True
        self.timeline_var.set(index)
        self.setting_timeline = False
        self._update_time()
        self._schedule_preview()

    def _timeline_changed(self, position: str) -> None:
        if self.setting_timeline or self.capture is None:
            return
        # Programmatic timeline movement during playback is not a user seek.
        if abs(float(position) - self.current_frame) < 0.5:
            return
        self._pause()
        if self.seek_job:
            self.root.after_cancel(self.seek_job)
        index = int(round(float(position)))
        self.seek_job = self.root.after(65, lambda: self._read_at(index))

    def _update_time(self) -> None:
        current = self.current_frame / self.fps
        total = self.frame_count / self.fps if self.frame_count else current
        def fmt(seconds: float) -> str:
            return f"{int(seconds // 60):02d}:{int(seconds % 60):02d}"
        self.timeline_text.set(f"{fmt(current)} / {fmt(total)}")

    def _schedule_preview(self) -> None:
        if self.source_frame is None:
            return
        if self.preview_job:
            self.root.after_cancel(self.preview_job)
        self.preview_job = self.root.after(35, self._render_preview)

    def _to_photo(self, bgr_frame) -> ImageTk.PhotoImage:
        rgb = cv2.cvtColor(bgr_frame, cv2.COLOR_BGR2RGB)
        return ImageTk.PhotoImage(Image.fromarray(rgb))

    def _small_frame(self, frame):
        height, width = frame.shape[:2]
        scale = min(
            self.PREVIEW_WIDTH / width,
            self.PREVIEW_HEIGHT / height,
            1.0,
        )
        if scale == 1.0:
            return frame
        return cv2.resize(
            frame, (max(1, round(width * scale)), max(1, round(height * scale))),
            interpolation=cv2.INTER_AREA,
        )

    def _render_preview(self) -> None:
        self.preview_job = None
        if self.source_frame is None:
            return
        try:
            settings = self._get_settings()
            original = self._small_frame(self.source_frame)
            corrected = process_frame(original, settings)
            self.original_photo = self._to_photo(original)
            self.adjusted_photo = self._to_photo(corrected)
            self.original_label.configure(image=self.original_photo, text="")
            self.adjusted_label.configure(image=self.adjusted_photo, text="")
        except (ValueError, cv2.error) as exc:
            self.status.set(f"Preview settings error: {exc}")

    def _toggle_play(self) -> None:
        if self.capture is None:
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
        if not self.playing or self.capture is None:
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
        # Do not debounce each playback frame away at 30+ FPS.
        self._render_preview()
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
        """Let the user select a neutral-color patch on the paused source frame."""
        if self.source_frame is None:
            messagebox.showinfo("Calibration", "Open a video first.")
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
            image, (max(1, round(width * scale)), max(1, round(height * scale))),
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
            popup, width=shown.shape[1], height=shown.shape[0],
            highlightthickness=0, cursor="crosshair",
        )
        canvas.pack(padx=10, pady=(0, 10))
        photo = self._to_photo(shown)
        canvas.image = photo  # Keep a strong reference to the Tk image.
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
                start[0], start[1], event.x, event.y,
                outline="yellow", width=2,
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
                round(x1 / scale), round(y1 / scale),
                round(x2 / scale), round(y2 / scale),
            )
            try:
                gains = measure_balance_offset(
                    image, coords, self.calibration_hex.get().strip()
                )
            except ValueError as exc:
                messagebox.showerror("Calibration", str(exc), parent=popup)
                return
            for key, gain in zip(
                ("blue_gain", "green_gain", "red_gain"), gains
            ):
                slider = self.controls[key]
                if gain > float(slider.cget("to")):
                    slider.configure(to=min(255.0, round(gain * 1.2, 2)))
                self.values[key].set(gain)
            self.status.set(
                "Calibrated fixed B/G/R gains: "
                + ", ".join(f"{value:.3f}" for value in gains)
            )
            self._schedule_preview()
            popup.destroy()

        canvas.bind("<ButtonPress-1>", press)
        canvas.bind("<B1-Motion>", drag)
        canvas.bind("<ButtonRelease-1>", release)

    def _reset(self) -> None:
        for name, _, _, default, _ in CONTROL_SPECS:
            self.values[name].set(default)
        self.remap_enabled.set(False)
        self.remap_hex.set("FF8800")
        self.calibration_hex.set("FFFFFF")
        self._schedule_preview()

    def _save_preset(self) -> None:
        try:
            settings = self._get_settings()
        except ValueError as exc:
            messagebox.showerror("Preset", str(exc))
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".json", filetypes=[("JSON preset", "*.json")]
        )
        if not path:
            return
        Path(path).write_text(
            json.dumps({
                "settings": asdict(settings),
                "calibration_hex": self.calibration_hex.get(),
                "remap_hex": self.remap_hex.get(),
            }, indent=2), encoding="utf-8"
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
                self.values[name].set(getattr(settings, name))
            for name, gain in zip(
                ("blue_gain", "green_gain", "red_gain"),
                settings.offset or (1.0, 1.0, 1.0),
            ):
                slider = self.controls[name]
                if gain > float(slider.cget("to")):
                    slider.configure(to=min(255.0, round(gain * 1.2, 2)))
                self.values[name].set(gain)
            self.values["hue_low"].set(settings.blue_range[0])
            self.values["hue_high"].set(settings.blue_range[1])
            self.remap_enabled.set(bool(settings.remap_blue))
            self.remap_hex.set(
                settings.remap_blue or payload.get("remap_hex", "FF8800")
            )
            self.calibration_hex.set(payload.get("calibration_hex", "FFFFFF"))
            self._schedule_preview()
            self.status.set(f"Loaded preset: {path}")
        except (OSError, KeyError, TypeError, ValueError) as exc:
            messagebox.showerror("Load preset", f"Invalid preset: {exc}")

    def _start_export(self) -> None:
        if self.exporting or self.source_path is None:
            if self.source_path is None:
                messagebox.showinfo("Export", "Open a video first.")
            return
        try:
            settings = self._get_settings()
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
            title="Export corrected MP4", initialfile=suggested,
            defaultextension=".mp4", filetypes=[("MP4 video", "*.mp4")],
        )
        if not path:
            return
        destination = Path(path)
        source_for_export = self.source_path  # Snapshot before worker starts.
        if destination.resolve() == source_for_export.resolve():
            messagebox.showerror("Export", "Choose a different output filename.")
            return

        self._pause()
        self.cancel_event.clear()
        self.exporting = True
        self.export_button.configure(state="disabled")
        self.cancel_button.configure(state="normal")
        self.progress_bar.configure(value=0)
        self.export_started_at = time.monotonic()
        self.export_last_update_at = self.export_started_at
        self.export_stage = "Starting export"
        self.export_progress = ""
        self.status.set("Starting export. The interface remains available...")

        def worker() -> None:
            try:
                export_video(
                    source_for_export, destination, settings, self.cancel_event,
                    lambda done, total: self.events.put(("progress", done, total)),
                    lambda stage: self.events.put(("stage", stage)),
                )
                self.events.put(("success", str(destination)))
            except InterruptedError:
                self.events.put(("canceled",))
            except Exception as exc:
                self.events.put(("error", str(exc)))

        self.export_thread = threading.Thread(target=worker, daemon=True)
        self.export_thread.start()

    def _cancel_export(self) -> None:
        """Signal cancellation; the export watcher interrupts blocked FFmpeg."""
        if not self.exporting:
            return
        self.cancel_event.set()
        self.cancel_button.configure(state="disabled")
        self.export_stage = "Canceling export"
        self.status.set("Canceling export and cleaning temporary files...")

    def _poll_events(self) -> None:
        """Handle worker messages only on the Tkinter main thread."""
        try:
            # Bound work per poll so a busy worker cannot starve Tk events.
            for _ in range(100):
                event = self.events.get_nowait()
                self.export_last_update_at = time.monotonic()
                kind = event[0]
                if kind == "progress":
                    _, count, total = event
                    # Leave 10% for audio muxing and container finalization.
                    if total > 0:
                        self.progress_bar.configure(
                            value=min(90, 90 * count / total)
                        )
                    self.export_progress = (
                        f"Frame {count:,} / {total:,}" if total > 0
                        else f"Frame {count:,}"
                    )
                elif kind == "stage":
                    self.export_stage = event[1]
                    self.export_progress = ""
                    if self.export_stage.startswith("Muxing"):
                        self.progress_bar.configure(value=90)
                else:
                    self.exporting = False
                    self.export_button.configure(state="normal")
                    self.cancel_button.configure(state="disabled")
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
            # queue.get_nowait() raises queue.Empty before 100 iterations.
        except queue.Empty:
            pass

        if self.exporting:
            elapsed = int(time.monotonic() - self.export_started_at)
            details = f" | {self.export_progress}" if self.export_progress else ""
            self.status.set(
                f"{self.export_stage}{details} | elapsed {elapsed}s"
                + (" | still working..." if time.monotonic()
                   - self.export_last_update_at >= 3 else "")
            )
        self.root.after(150, self._poll_events)

    def _close(self) -> None:
        if self.exporting:
            if not messagebox.askyesno(
                "Export in progress", "Cancel the export and close?"
            ):
                return
            self._cancel_export()
        self._pause()
        if self.capture is not None:
            self.capture.release()
        self.root.destroy()


def main() -> None:
    """Start the native desktop application."""
    root = tk.Tk()
    VideoColorApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()