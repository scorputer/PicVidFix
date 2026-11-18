"""Reusable desktop photo/video preview with frame-index navigation.

Frame numbers are zero-based, consistent with OpenCV's frame seek API.
Each pane owns at most one independently opened VideoCapture and releases it.
Video frame previews are resized for the GUI; analysis uses separate copies.
"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import ttk
from typing import Callable

import cv2
import numpy as np
from PIL import Image, ImageTk


def bounded_frame_index(value: int | str, frame_count: int) -> int:
    """Validate the frame entry, never silently use a different frame."""
    if frame_count < 1:
        raise ValueError("Video has no decodable frame count.")
    try:
        index = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Enter a whole-number frame index.") from exc
    if str(value).strip() != str(index):
        raise ValueError("Enter a whole-number frame index.")
    if index < 0 or index >= frame_count:
        raise ValueError(f"Frame must be between 0 and {frame_count - 1}.")
    return index


def frame_caption(
    filename: str,
    frame_index: int | None,
    frame_count: int | None,
) -> str:
    """Make video frame number visible in every comparison pane."""
    name = Path(filename).name if filename else "Image"
    if frame_index is None:
        return name + " | Photo"
    if frame_count is None or frame_count < 1:
        return f"{name} | Frame {frame_index}"
    return f"{name} | Frame {frame_index} / {frame_count - 1} (0-based)"


def preview_bgr(frame: np.ndarray, max_width: int = 440,
                max_height: int = 255) -> Image.Image:
    """Make a preview thumbnail while preserving the original BGR buffer."""
    if frame.dtype != np.uint8:
        raise ValueError("Previews require uint8 images.")
    if frame.ndim == 2:
        rgb = cv2.cvtColor(frame, cv2.COLOR_GRAY2RGB)
    elif frame.ndim == 3 and frame.shape[2] == 4:
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGRA2RGBA)
    elif frame.ndim == 3 and frame.shape[2] == 3:
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    else:
        raise ValueError("Unsupported image shape.")
    height, width = rgb.shape[:2]
    factor = min(1, max_width / width, max_height / height)
    if factor < 1:
        rgb = cv2.resize(
            rgb, (max(1, round(width * factor)),
                  max(1, round(height * factor))),
            interpolation=cv2.INTER_AREA,
        )
    return Image.fromarray(rgb)


class FramePreviewPanel(ttk.LabelFrame):
    """Source or target pane with scroll slider and editable video frame index."""

    def __init__(
        self,
        parent: tk.Misc,
        title: str,
        on_change: Callable[[int, np.ndarray], None],
    ) -> None:
        super().__init__(parent, text=title, padding=7)
        self.on_change = on_change
        self.frame: np.ndarray | None = None
        self.filename = ""
        self.video_path: str | None = None
        self.frame_count: int | None = None
        self.frame_index: int | None = None
        self.capture: cv2.VideoCapture | None = None
        self.photo: ImageTk.PhotoImage | None = None
        self.seek_job: str | None = None
        self._setting_slider = False

        self.caption = tk.StringVar(value="No image selected")
        ttk.Label(
            self, textvariable=self.caption, wraplength=420
        ).pack(fill="x", pady=(0, 5))

        self.preview = ttk.Label(
            self, text="No image selected", anchor="center"
        )
        self.preview.pack(fill="both", expand=True)

        self.navigation = ttk.Frame(self)
        self.navigation.pack(fill="x", pady=(6, 0))
        self.slider = ttk.Scale(
            self.navigation, from_=0, to=1,
            command=self._on_slider,
        )
        self.slider.pack(side="left", fill="x", expand=True)
        self.frame_entry = tk.StringVar(value="0")
        self.frame_field = ttk.Entry(
            self.navigation, textvariable=self.frame_entry, width=9,
        )
        self.frame_field.pack(side="left", padx=(8, 4))
        self.frame_field.bind("<Return>", self._on_entry)
        self.frame_field.bind("<FocusOut>", self._on_entry)
        ttk.Label(self.navigation, text="frame").pack(side="left")
        self.feedback = tk.StringVar(value="")
        ttk.Label(
            self, textvariable=self.feedback, foreground="#6a7990"
        ).pack(fill="x", pady=(3, 0))
        self.navigation.pack_forget()

    def clear(self) -> None:
        """Clear the image and release the previous video decoder."""
        if self.seek_job is not None:
            self.after_cancel(self.seek_job)
            self.seek_job = None
        if self.capture is not None:
            self.capture.release()
            self.capture = None
        self.frame = None
        self.filename = ""
        self.video_path = None
        self.frame_count = None
        self.frame_index = None
        self.photo = None
        self.preview.configure(image="", text="No image selected")
        self.caption.set("No image selected")
        self.navigation.pack_forget()
        self.feedback.set("")

    def set_frame(
        self,
        frame: np.ndarray,
        filename: str,
        *,
        video_path: str | None = None,
        frame_index: int | None = None,
        frame_count: int | None = None,
    ) -> None:
        """Replace the displayed frame without implicitly changing selection."""
        if self.video_path != video_path and self.capture is not None:
            self.capture.release()
            self.capture = None
        self.filename = filename
        self.video_path = video_path
        self.frame_count = max(1, frame_count or 1) if video_path else None
        self.frame_index = frame_index if video_path else None
        self.frame = frame.copy()
        self.photo = ImageTk.PhotoImage(preview_bgr(frame))
        self.preview.configure(image=self.photo, text="")
        self.caption.set(frame_caption(
            filename, self.frame_index, self.frame_count
        ))
        self.feedback.set("")
        if video_path is not None:
            self._setting_slider = True
            self.slider.configure(to=max(0, self.frame_count - 1))
            self.slider.set(frame_index or 0)
            self.frame_entry.set(str(frame_index or 0))
            self._setting_slider = False
            self.navigation.pack(fill="x", pady=(6, 0))
        else:
            self.navigation.pack_forget()

    def selected_frame(self) -> np.ndarray | None:
        """Return an independent analysis snapshot, never a shared view."""
        return None if self.frame is None else self.frame.copy()

    def _on_slider(self, value: str) -> None:
        if self._setting_slider or self.video_path is None:
            return
        index = min(self.frame_count - 1, max(0, int(round(float(value)))))
        self.frame_entry.set(str(index))
        if self.seek_job is not None:
            self.after_cancel(self.seek_job)
        self.seek_job = self.after(130, lambda: self._seek(index))

    def _on_entry(self, _event=None) -> None:
        if self.video_path is None:
            return
        try:
            index = bounded_frame_index(
                self.frame_entry.get(), self.frame_count
            )
        except ValueError as error:
            self.feedback.set(str(error))
            self.frame_entry.set(str(self.frame_index or 0))
            return
        if self.seek_job is not None:
            self.after_cancel(self.seek_job)
            self.seek_job = None
        self._seek(index)

    def _seek(self, index: int) -> None:
        self.seek_job = None
        if self.video_path is None:
            return
        if index == self.frame_index:
            return
        if self.capture is None:
            self.capture = cv2.VideoCapture(self.video_path)
        if not self.capture.isOpened():
            self.feedback.set("Cannot open video decoder.")
            return
        self.capture.set(cv2.CAP_PROP_POS_FRAMES, index)
        ok, frame = self.capture.read()
        if not ok:
            self.feedback.set(f"Unable to decode frame {index}.")
            self.frame_entry.set(str(self.frame_index or 0))
            return
        self.set_frame(
            frame, self.filename, video_path=self.video_path,
            frame_index=index, frame_count=self.frame_count,
        )
        self.on_change(index, frame.copy())

    def destroy(self) -> None:
        self.clear()
        super().destroy()
