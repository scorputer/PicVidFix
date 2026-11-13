"""Full-resolution image ROI inspector for the PicVidFix desktop application.

This window deliberately uses a tiled viewport rather than building a huge
Tk PhotoImage when a user zooms into a 5K or larger image. The source image
is never altered by drawing a bounding box.
"""

from __future__ import annotations

import json
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk
from typing import Callable

import cv2
from PIL import Image, ImageTk


class RegionInspector:
    """Zoom, pan, inspect pixels, and annotate one image rectangle."""

    BACKGROUND = (16, 22, 33)
    MAX_ZOOM = 8.0
    MIN_ZOOM = 0.02

    def __init__(
        self,
        parent: tk.Misc,
        frame,
        filename: str,
        on_box: Callable[[tuple[int, int, int, int]], None],
        initial_box: tuple[int, int, int, int] | None = None,
        frame_index: int | None = None,
    ) -> None:
        self.parent = parent
        self.filename = filename
        self.frame_index = frame_index
        self.on_box = on_box

        if frame.ndim == 2:
            rgb = cv2.cvtColor(frame, cv2.COLOR_GRAY2RGB)
        elif frame.ndim == 3 and frame.shape[2] == 4:
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGRA2RGBA)
        else:
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        self.image = Image.fromarray(rgb).convert("RGB")
        self.image_width, self.image_height = self.image.size

        self.box = initial_box
        self.scale = 1.0
        self.offset_x = 0.0
        self.offset_y = 0.0
        self.mode = "box"
        self.space_down = False
        self.drag_start: tuple[int, int] | None = None
        self.pan_start: tuple[float, float, float, float] | None = None
        self.display_image = None
        self.render_pending = False
        self.initial_fit_done = False

        self.window = tk.Toplevel(parent)
        self.window.title("PicVidFix | Region inspector")
        self.window.geometry("1080x780")
        self.window.minsize(640, 400)
        self.window.protocol("WM_DELETE_WINDOW", self.window.destroy)

        toolbar = ttk.Frame(self.window, padding=8)
        toolbar.pack(side="top", fill="x")
        ttk.Button(toolbar, text="Fit", command=self.fit).pack(
            side="left", padx=(0, 4)
        )
        ttk.Button(toolbar, text="100%", command=lambda: self.zoom_to(1.0)).pack(
            side="left", padx=(0, 4)
        )
        ttk.Button(toolbar, text="−", command=lambda: self.zoom_by(1 / 1.3)).pack(
            side="left", padx=(0, 4)
        )
        ttk.Button(toolbar, text="+", command=lambda: self.zoom_by(1.3)).pack(
            side="left", padx=(0, 10)
        )
        self.zoom_text = tk.StringVar(value="Fit")
        ttk.Label(toolbar, textvariable=self.zoom_text, width=10).pack(
            side="left"
        )
        ttk.Button(toolbar, text="Draw box", command=self.select_mode).pack(
            side="left", padx=(10, 5)
        )
        ttk.Button(toolbar, text="Pan", command=self.pan_mode).pack(
            side="left", padx=(0, 5)
        )
        ttk.Button(toolbar, text="Clear box", command=self.clear_box).pack(
            side="left", padx=(0, 5)
        )
        ttk.Button(
            toolbar, text="Export box JSON", command=self.export_box
        ).pack(side="right")

        self.canvas = tk.Canvas(
            self.window,
            width=1000,
            height=600,
            highlightthickness=0,
            bg="#101621",
            cursor="crosshair",
        )
        self.canvas.pack(fill="both", expand=True, padx=8, pady=5)

        footer = ttk.Frame(self.window, padding=(8, 3, 8, 8))
        footer.pack(fill="x")
        self.position_text = tk.StringVar(value="X: —  Y: —")
        self.region_text = tk.StringVar(value="No region selected")
        self.mode_text = tk.StringVar(value="Draw box · Space + drag to pan")
        ttk.Label(footer, textvariable=self.position_text, width=26).pack(
            side="left"
        )
        ttk.Label(footer, textvariable=self.region_text).pack(
            side="left", padx=(10, 0)
        )
        ttk.Label(footer, textvariable=self.mode_text).pack(side="right")

        self.canvas.bind("<Configure>", self._on_resize)
        self.canvas.bind("<Motion>", self._on_pointer)
        self.canvas.bind("<Leave>", self._on_leave)
        self.canvas.bind("<ButtonPress-1>", self._press_left)
        self.canvas.bind("<B1-Motion>", self._drag_left)
        self.canvas.bind("<ButtonRelease-1>", self._release_left)
        self.canvas.bind("<ButtonPress-2>", self._press_pan)
        self.canvas.bind("<B2-Motion>", self._drag_pan)
        self.canvas.bind("<ButtonRelease-2>", self._release_pan)
        self.canvas.bind("<MouseWheel>", self._wheel)
        self.canvas.bind("<Button-4>", lambda e: self.zoom_by(1.2, e.x, e.y))
        self.canvas.bind("<Button-5>", lambda e: self.zoom_by(1 / 1.2, e.x, e.y))
        self.window.bind("<KeyPress-space>", self._space_press)
        self.window.bind("<KeyRelease-space>", self._space_release)
        self.window.bind("<Escape>", self._escape)

        self._show_region()
        self.window.after(80, self.fit)

    def _view_size(self) -> tuple[int, int]:
        return max(1, self.canvas.winfo_width()), max(
            1, self.canvas.winfo_height()
        )

    def _on_resize(self, _event=None) -> None:
        if not self.initial_fit_done:
            return
        self._queue_render()

    def _queue_render(self) -> None:
        if self.render_pending:
            return
        self.render_pending = True
        self.window.after(20, self._render)

    def _render(self) -> None:
        self.render_pending = False
        if not self.window.winfo_exists():
            return
        view_width, view_height = self._view_size()
        # Inverse affine mapping crops only pixels visible in the viewport.
        # A 5000x3500 source never becomes a 5000x3500 Tk PhotoImage.
        inverse = (
            1.0 / self.scale,
            0.0,
            -self.offset_x / self.scale,
            0.0,
            1.0 / self.scale,
            -self.offset_y / self.scale,
        )
        viewport = self.image.transform(
            (view_width, view_height),
            Image.Transform.AFFINE,
            inverse,
            resample=Image.Resampling.BILINEAR,
            fillcolor=self.BACKGROUND,
        )
        self.display_image = ImageTk.PhotoImage(viewport)
        self.canvas.delete("all")
        self.canvas.create_image(
            0, 0, image=self.display_image, anchor="nw", tags=("photo",)
        )
        self._draw_box()
        self.zoom_text.set(f"{self.scale * 100:.1f}%")

    def _draw_box(self) -> None:
        self.canvas.delete("roi")
        if self.box is None:
            return
        x, y, width, height = self.box
        x0 = self.offset_x + x * self.scale
        y0 = self.offset_y + y * self.scale
        x1 = self.offset_x + (x + width) * self.scale
        y1 = self.offset_y + (y + height) * self.scale
        self.canvas.create_rectangle(
            x0, y0, x1, y1, outline="#86eac3",
            width=2, tags="roi"
        )
        for cx, cy in ((x0, y0), (x1, y0), (x1, y1), (x0, y1)):
            self.canvas.create_oval(
                cx - 4, cy - 4, cx + 4, cy + 4,
                outline="#101621", fill="#86eac3", tags="roi",
            )

    def _show_region(self) -> None:
        if self.box is None:
            self.region_text.set("No region selected")
        else:
            x, y, width, height = self.box
            self.region_text.set(
                f"X: {x}  Y: {y}  W: {width}  H: {height}"
            )

    def _image_point(self, x: float, y: float) -> tuple[int, int]:
        px = int((x - self.offset_x) // self.scale)
        py = int((y - self.offset_y) // self.scale)
        return (
            max(0, min(self.image_width - 1, px)),
            max(0, min(self.image_height - 1, py)),
        )

    def _on_pointer(self, event) -> None:
        x, y = self._image_point(event.x, event.y)
        self.position_text.set(f"X: {x:,}  Y: {y:,}")

    def _on_leave(self, _event) -> None:
        self.position_text.set("X: —  Y: —")

    def fit(self) -> None:
        view_width, view_height = self._view_size()
        self.scale = min(
            1.0, view_width / self.image_width,
            view_height / self.image_height
        )
        self.offset_x = (view_width - self.image_width * self.scale) / 2
        self.offset_y = (view_height - self.image_height * self.scale) / 2
        self.initial_fit_done = True
        self._queue_render()

    def zoom_to(self, value: float, x=None, y=None) -> None:
        view_width, view_height = self._view_size()
        cursor_x = view_width / 2 if x is None else x
        cursor_y = view_height / 2 if y is None else y
        source_x = (cursor_x - self.offset_x) / self.scale
        source_y = (cursor_y - self.offset_y) / self.scale
        self.scale = min(self.MAX_ZOOM, max(self.MIN_ZOOM, value))
        self.offset_x = cursor_x - source_x * self.scale
        self.offset_y = cursor_y - source_y * self.scale
        self._queue_render()

    def zoom_by(self, factor: float, x=None, y=None) -> None:
        self.zoom_to(self.scale * factor, x, y)

    def _wheel(self, event) -> None:
        self.zoom_by(1.2 if event.delta > 0 else 1 / 1.2, event.x, event.y)

    def select_mode(self) -> None:
        self.mode = "box"
        self.canvas.configure(cursor="crosshair")
        self.mode_text.set("Draw box · Space + drag to pan")

    def pan_mode(self) -> None:
        self.mode = "pan"
        self.canvas.configure(cursor="fleur")
        self.mode_text.set("Pan · click and drag")

    def _space_press(self, _event) -> None:
        self.space_down = True

    def _space_release(self, _event) -> None:
        self.space_down = False

    def _escape(self, _event) -> None:
        self.drag_start = None
        self.pan_start = None
        self._queue_render()

    def _press_pan(self, event) -> None:
        self.pan_start = (
            event.x, event.y, self.offset_x, self.offset_y
        )

    def _drag_pan(self, event) -> None:
        if self.pan_start is None:
            return
        x0, y0, offset_x, offset_y = self.pan_start
        self.offset_x = offset_x + event.x - x0
        self.offset_y = offset_y + event.y - y0
        self._queue_render()
        self._on_pointer(event)

    def _release_pan(self, _event) -> None:
        self.pan_start = None

    def _press_left(self, event) -> None:
        if self.mode == "pan" or self.space_down:
            self._press_pan(event)
            return
        self.drag_start = self._image_point(event.x, event.y)
        self.box = (
            self.drag_start[0], self.drag_start[1], 1, 1
        )
        self._show_region()
        self._draw_box()

    def _drag_left(self, event) -> None:
        if self.pan_start is not None:
            self._drag_pan(event)
            return
        if self.drag_start is None:
            return
        bx, by = self._image_point(event.x, event.y)
        ax, ay = self.drag_start
        self.box = (
            min(ax, bx), min(ay, by),
            abs(bx - ax) + 1, abs(by - ay) + 1
        )
        self._show_region()
        self._draw_box()
        self._on_pointer(event)

    def _release_left(self, event) -> None:
        if self.pan_start is not None:
            self._release_pan(event)
            return
        if self.drag_start is None:
            return
        self._drag_left(event)
        self.drag_start = None
        if self.box is not None:
            self.on_box(self.box)

    def clear_box(self) -> None:
        self.box = None
        self.drag_start = None
        self._show_region()
        self._draw_box()

    def export_box(self) -> None:
        if self.box is None:
            self.mode_text.set("Draw a box before exporting JSON.")
            return
        x, y, width, height = self.box
        filename = Path(self.filename).stem + "_region.json"
        target = filedialog.asksaveasfilename(
            parent=self.window, title="Export region coordinates",
            defaultextension=".json", initialfile=filename,
            filetypes=[("JSON", "*.json")],
        )
        if not target:
            return
        metadata = {
            "schema": "picvidfix-region-v1",
            "image": {
                "filename": self.filename,
                "width": self.image_width,
                "height": self.image_height,
            },
            "coordinateSystem": "zero-based output pixels; x2/y2 exclusive",
            "region": {
                "x": x, "y": y, "width": width, "height": height,
                "x2": x + width, "y2": y + height,
            },
            "normalized": {
                "x": x / self.image_width,
                "y": y / self.image_height,
                "width": width / self.image_width,
                "height": height / self.image_height,
            },
        }
        if self.frame_index is not None:
            metadata["image"]["frame_index"] = self.frame_index
        Path(target).write_text(
            json.dumps(metadata, indent=2), encoding="utf-8"
        )
        self.mode_text.set("Exported " + Path(target).name)
