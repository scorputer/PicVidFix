"""Interactive object selection and mask refinement for PicVidFix Desktop.

The user draws a rectangle, runs OpenCV GrabCut, and paints keep/remove
corrections. Mouse wheel zooms around the pointer, and Space+drag pans.
Object segmentation happens on an analysis-sized image to keep 5K sources
responsive. Full-resolution processing upsamples the chosen mask.
"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable

import cv2
import numpy as np
from PIL import Image, ImageTk

from object_mask import paint_object_mask, segment_object


MAX_OBJECT_SIDE = 1200


def prepare_selection_image(image: np.ndarray) -> np.ndarray:
    """Return a BGR copy scaled to a practical interactive working size."""
    if image.dtype != np.uint8:
        raise ValueError("Object selection needs an 8-bit image.")
    if image.ndim == 2:
        image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    elif image.ndim == 3 and image.shape[2] in (3, 4):
        image = image[:, :, :3]
    else:
        raise ValueError("Expected grayscale, BGR, or BGRA image.")

    height, width = image.shape[:2]
    factor = min(1.0, MAX_OBJECT_SIDE / max(width, height))
    if factor < 1:
        return cv2.resize(
            image,
            (max(4, round(width * factor)), max(4, round(height * factor))),
            interpolation=cv2.INTER_AREA,
        )
    return image.copy()


def original_pixel(
    position: tuple[int, int],
    selection_dimensions: tuple[int, int],
    original_dimensions: tuple[int, int],
) -> tuple[int, int]:
    """Map a point in the working image to native source pixel coordinates."""
    x, y = position
    work_width, work_height = selection_dimensions
    width, height = original_dimensions
    if min(work_width, work_height, width, height) <= 0:
        raise ValueError("Image dimensions must be positive.")
    return (
        max(0, min(width - 1, int(x * width / work_width))),
        max(0, min(height - 1, int(y * height / work_height))),
    )


class ObjectSelectionDialog:
    """Select one object using a bounding box and refine its segmentation."""

    def __init__(
        self,
        parent: tk.Misc,
        frame: np.ndarray,
        on_accept: Callable[[np.ndarray], None],
        existing_mask: np.ndarray | None = None,
        *,
        frame_label: str = "",
    ) -> None:
        self.on_accept = on_accept
        self.source_height, self.source_width = frame.shape[:2]
        self.image = prepare_selection_image(frame)
        self.work_height, self.work_width = self.image.shape[:2]
        self.mask: np.ndarray | None = None
        if existing_mask is not None:
            self.mask = cv2.resize(
                existing_mask, (self.work_width, self.work_height),
                interpolation=cv2.INTER_LINEAR,
            ).astype(np.uint8)
        self.box: tuple[int, int, int, int] | None = None
        self.start: tuple[int, int] | None = None
        self.pan_origin: tuple[float, float, float, float] | None = None
        self.space_held = False
        self.scale = 1.0
        self.offset_x = 0.0
        self.offset_y = 0.0
        self.photo: ImageTk.PhotoImage | None = None
        self.render_job: str | None = None
        self.results: queue.Queue = queue.Queue()
        self.busy = False
        self.request_id = 0

        self.window = tk.Toplevel(parent)
        self.window.title("PicVidFix | Select object" + (
            " | " + frame_label if frame_label else ""
        ))
        self.window.geometry("1140x820")
        self.window.minsize(720, 490)
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        # Keep the source frame stable while a mask is being drawn.
        # This prevents attaching an old object's mask to a new image/frame.
        self.window.after_idle(
            lambda: self.window.grab_set()
            if self.window.winfo_exists() else None
        )

        top = ttk.Frame(self.window, padding=9)
        top.pack(fill="x")
        ttk.Label(
            top,
            text="1. Draw a loose box around the object. "
            "2. Find object. 3. Refine with Keep/Remove brush. "
            "4. Use selection.",
            wraplength=1060,
        ).pack(anchor="w")
        ttk.Label(
            top,
            text="Mouse wheel = zoom | Space + drag = pan | "
            "Mask is an editable analysis overlay, not burned into the image.",
            foreground="#666666",
        ).pack(anchor="w", pady=(3, 0))

        tools = ttk.Frame(self.window, padding=(9, 2, 9, 6))
        tools.pack(fill="x")
        self.mode = tk.StringVar(value="box")
        for name, label in (
            ("box", "Draw box"), ("keep", "Keep brush"),
            ("remove", "Remove brush"),
        ):
            ttk.Radiobutton(
                tools, text=label, value=name, variable=self.mode,
            ).pack(side="left", padx=(0, 7))
        ttk.Label(tools, text="Brush radius").pack(side="left", padx=(10, 3))
        self.brush_radius = tk.IntVar(value=12)
        ttk.Spinbox(
            tools, textvariable=self.brush_radius, from_=1, to=80,
            width=5,
        ).pack(side="left")
        ttk.Button(
            tools, text="Fit", command=self.fit
        ).pack(side="right", padx=(5, 0))
        ttk.Button(
            tools, text="100%", command=lambda: self.zoom_to(1.0)
        ).pack(side="right", padx=(5, 0))
        ttk.Button(
            tools, text="+", command=lambda: self.zoom_by(1.35)
        ).pack(side="right", padx=(5, 0))
        ttk.Button(
            tools, text="−", command=lambda: self.zoom_by(1 / 1.35)
        ).pack(side="right", padx=(5, 0))

        self.canvas = tk.Canvas(
            self.window, bg="#111a27", highlightthickness=0,
            cursor="crosshair",
        )
        self.canvas.pack(fill="both", expand=True, padx=9, pady=(0, 6))
        self.canvas.bind("<Configure>", self._resized)
        self.canvas.bind("<Motion>", self._move)
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._drag)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.canvas.bind("<MouseWheel>", self._wheel)
        self.window.bind("<KeyPress-space>", self._space_down)
        self.window.bind("<KeyRelease-space>", self._space_up)
        self.window.bind("<Escape>", lambda _event: self.close())

        bottom = ttk.Frame(self.window, padding=(9, 0, 9, 9))
        bottom.pack(fill="x")
        self.status = tk.StringVar(
            value="Draw around an object, leaving some visible background."
        )
        self.position = tk.StringVar(value="X: —  Y: —")
        ttk.Label(
            bottom, textvariable=self.position, width=27,
        ).pack(side="left")
        ttk.Label(
            bottom, textvariable=self.status, wraplength=430,
        ).pack(side="left", fill="x", expand=True)
        ttk.Button(
            bottom, text="Cancel", command=self.close
        ).pack(side="right")
        self.use_button = ttk.Button(
            bottom, text="Use selection", command=self.accept,
            state="normal" if self.mask is not None else "disabled",
        )
        self.use_button.pack(side="right", padx=(0, 5))
        self.find_button = ttk.Button(
            bottom, text="Find object", command=self.find_object,
        )
        self.find_button.pack(side="right", padx=(0, 5))
        ttk.Button(
            bottom, text="Clear", command=self.clear
        ).pack(side="right", padx=(0, 5))

        self.window.after(80, self.fit)
        self.window.after(120, self._poll)

    def _resized(self, _event) -> None:
        self._schedule_render()

    def _size(self) -> tuple[int, int]:
        return max(1, self.canvas.winfo_width()), max(
            1, self.canvas.winfo_height()
        )

    def fit(self) -> None:
        width, height = self._size()
        self.scale = min(
            1.0, width / self.work_width, height / self.work_height
        )
        self.offset_x = (width - self.work_width * self.scale) / 2
        self.offset_y = (height - self.work_height * self.scale) / 2
        self._schedule_render()

    def zoom_to(self, scale: float, x: float | None = None,
                y: float | None = None) -> None:
        width, height = self._size()
        anchor_x = width / 2 if x is None else x
        anchor_y = height / 2 if y is None else y
        source_x = (anchor_x - self.offset_x) / self.scale
        source_y = (anchor_y - self.offset_y) / self.scale
        self.scale = min(8.0, max(0.04, scale))
        self.offset_x = anchor_x - source_x * self.scale
        self.offset_y = anchor_y - source_y * self.scale
        self._schedule_render()

    def zoom_by(self, factor: float, x=None, y=None) -> None:
        self.zoom_to(self.scale * factor, x, y)

    def _wheel(self, event) -> None:
        self.zoom_by(1.22 if event.delta > 0 else 1 / 1.22, event.x, event.y)

    def _space_down(self, _event) -> None:
        self.space_held = True

    def _space_up(self, _event) -> None:
        self.space_held = False

    def _point(self, event) -> tuple[int, int]:
        x = int((event.x - self.offset_x) // self.scale)
        y = int((event.y - self.offset_y) // self.scale)
        return (
            max(0, min(self.work_width - 1, x)),
            max(0, min(self.work_height - 1, y)),
        )

    def _move(self, event) -> None:
        px, py = original_pixel(
            self._point(event),
            (self.work_width, self.work_height),
            (self.source_width, self.source_height),
        )
        self.position.set(f"X: {px:,}  Y: {py:,}")

    def _press(self, event) -> None:
        if self.busy:
            return
        if self.space_held:
            self.pan_origin = (
                event.x, event.y, self.offset_x, self.offset_y
            )
            return
        point = self._point(event)
        if self.mode.get() == "box":
            self.start = point
            self.box = (*point, 1, 1)
            self._schedule_render()
        elif self.mask is not None:
            self._paint(point)

    def _drag(self, event) -> None:
        self._move(event)
        if self.pan_origin is not None:
            x, y, old_x, old_y = self.pan_origin
            self.offset_x = old_x + event.x - x
            self.offset_y = old_y + event.y - y
            self._schedule_render()
            return
        if self.start is not None and self.mode.get() == "box":
            current = self._point(event)
            ax, ay = self.start
            bx, by = current
            self.box = (
                min(ax, bx), min(ay, by),
                abs(ax - bx) + 1, abs(ay - by) + 1,
            )
            self._schedule_render()
        elif self.mask is not None and self.mode.get() != "box":
            self._paint(self._point(event))

    def _release(self, _event) -> None:
        self.start = None
        self.pan_origin = None

    def _paint(self, point: tuple[int, int]) -> None:
        if self.mask is None:
            return
        radius = max(1, min(80, self.brush_radius.get()))
        self.mask = paint_object_mask(
            self.mask, *point, radius=radius,
            foreground=self.mode.get() == "keep",
        )
        self._schedule_render()

    def find_object(self) -> None:
        if self.busy or self.box is None:
            self.status.set("First draw a rectangle around the object.")
            return
        self.busy = True
        self.request_id += 1
        identifier = self.request_id
        box = self.box
        self.find_button.configure(state="disabled")
        self.status.set("Finding object edges from the selected rectangle...")

        def worker() -> None:
            try:
                mask = segment_object(self.image, box)
                self.results.put((identifier, mask, None))
            except (ValueError, cv2.error) as error:
                self.results.put((identifier, None, str(error)))

        threading.Thread(target=worker, daemon=True).start()

    def _poll(self) -> None:
        if not self.window.winfo_exists():
            return
        try:
            while True:
                identifier, mask, error = self.results.get_nowait()
                if identifier != self.request_id:
                    continue
                self.busy = False
                self.find_button.configure(state="normal")
                if error:
                    self.status.set(error)
                    continue
                self.mask = mask
                self.use_button.configure(state="normal")
                self.status.set(
                    "Mask ready. Inspect edges, use Keep/Remove brush, "
                    "then click Use selection."
                )
                self._schedule_render()
        except queue.Empty:
            pass
        self.window.after(120, self._poll)

    def _schedule_render(self) -> None:
        if self.render_job is None and self.window.winfo_exists():
            self.render_job = self.window.after(40, self._render)

    def _render(self) -> None:
        self.render_job = None
        if not self.window.winfo_exists():
            return
        view_width, view_height = self._size()
        image = self.image.copy()
        if self.mask is not None:
            indices = self.mask > 64
            tint = np.array([180, 220, 75], dtype=np.float32)  # BGR teal
            image[indices] = np.clip(
                image[indices].astype(np.float32) * 0.60
                + tint * 0.40, 0, 255,
            ).astype(np.uint8)
        rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        transform = (
            1 / self.scale, 0, -self.offset_x / self.scale,
            0, 1 / self.scale, -self.offset_y / self.scale,
        )
        visible = Image.fromarray(rgb).transform(
            (view_width, view_height), Image.Transform.AFFINE,
            transform, resample=Image.Resampling.BILINEAR,
            fillcolor=(17, 26, 39),
        )
        self.photo = ImageTk.PhotoImage(visible)
        self.canvas.delete("all")
        self.canvas.create_image(
            0, 0, anchor="nw", image=self.photo,
        )
        if self.box is not None:
            x, y, width, height = self.box
            x1, y1 = (
                self.offset_x + x * self.scale,
                self.offset_y + y * self.scale,
            )
            x2 = self.offset_x + (x + width) * self.scale
            y2 = self.offset_y + (y + height) * self.scale
            self.canvas.create_rectangle(
                x1, y1, x2, y2, outline="#f3d769", width=2,
            )

    def clear(self) -> None:
        if self.busy:
            return
        self.box = None
        self.mask = None
        self.use_button.configure(state="disabled")
        self.status.set("Selection cleared. Draw a rectangle to start again.")
        self._schedule_render()

    def accept(self) -> None:
        if self.busy or self.mask is None or not np.any(self.mask):
            messagebox.showwarning(
                "Object selection", "Find or paint an object first.",
                parent=self.window,
            )
            return
        self.on_accept(self.mask.copy())
        self.close()

    def close(self) -> None:
        if not self.window.winfo_exists():
            return
        self.request_id += 1
        if self.render_job is not None:
            self.window.after_cancel(self.render_job)
            self.render_job = None
        if self.window.grab_current() == self.window:
            self.window.grab_release()
        self.window.destroy()
