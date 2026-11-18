"""Multi-reference color matching workbench for PicVidFix Desktop.

Collect photos and individual video frames, analyze same-scene overlap using
reference_match.py, and review a spatial exposure recommendation map.
No edits are applied until the user explicitly accepts an advisory suggestion.
"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Callable

import cv2
import numpy as np

from reference_match import (
    GRID_COLUMNS,
    GRID_ROWS,
    MAX_ANALYSIS_SIDE,
    ReferenceReport,
    analyze_references,
    format_report,
)

IMAGE_TYPES = (
    ("Photo files", "*.jpg *.jpeg *.png *.tif *.tiff *.bmp *.webp"),
    ("All files", "*.*"),
)
VIDEO_TYPES = (
    ("Videos", "*.mp4 *.mov *.avi *.mkv *.webm *.m4v"),
    ("All files", "*.*"),
)


def prepare_reference(frame: np.ndarray) -> np.ndarray:
    """Keep reference snapshots small enough for several to coexist in RAM."""
    height, width = frame.shape[:2]
    scale = min(1.0, MAX_ANALYSIS_SIDE / max(height, width))
    if scale >= 1.0:
        return frame.copy()
    return cv2.resize(
        frame, (round(width * scale), round(height * scale)),
        interpolation=cv2.INTER_AREA,
    )


def describe_reference(report: ReferenceReport) -> str:
    """Provide useful human interpretation without claiming automatic truth."""
    successful = sum(item.matched for item in report.references)
    return (
        f"{successful} of {len(report.references)} reference(s) aligned. "
        "A +EV tile suggests brightening the source; a -EV tile suggests "
        "darkening it. Blank tiles were not supported by matched pixels. "
        "Alignment confidence is a heuristic, not a calibrated probability."
    )


class ReferenceMatchDialog:
    """Desktop UI for reference collection, inspection, and optional adoption."""

    def __init__(
        self,
        parent: tk.Misc,
        source_provider: Callable[[], np.ndarray | None],
        source_label: Callable[[], str],
        apply_callback: Callable[[ReferenceReport], None],
    ) -> None:
        self.source_provider = source_provider
        self.source_label = source_label
        self.apply_callback = apply_callback
        self.references: list[tuple[str, np.ndarray]] = []
        self.results: queue.Queue = queue.Queue()
        self.report: ReferenceReport | None = None
        self.busy = False

        self.window = tk.Toplevel(parent)
        self.window.title("PicVidFix | Reference color analysis")
        self.window.geometry("1000x840")
        self.window.minsize(710, 580)

        header = ttk.Frame(self.window, padding=12)
        header.pack(fill="x")
        ttk.Label(
            header, text="Compare overlapping regions with reference photos "
            "or video frames.", wraplength=900,
        ).pack(anchor="w")
        ttk.Label(
            header,
            text="Analysis only until you explicitly apply a global suggestion. "
            "Unrelated scenes are not auto-matched.",
            wraplength=900,
            foreground="#666666",
        ).pack(anchor="w", pady=(4, 0))

        controls = ttk.Frame(self.window, padding=(12, 4, 12, 8))
        controls.pack(fill="x")
        ttk.Button(
            controls, text="+ Photo(s)", command=self.add_photos
        ).pack(side="left", padx=(0, 5))
        ttk.Button(
            controls, text="+ Video frame", command=self.add_video_frame
        ).pack(side="left", padx=(0, 5))
        ttk.Button(
            controls, text="+ Current frame", command=self.add_current_frame
        ).pack(side="left", padx=(0, 5))
        ttk.Button(
            controls, text="Remove selected", command=self.remove_selected
        ).pack(side="left", padx=(0, 5))
        self.analyze_button = ttk.Button(
            controls, text="Analyze overlap", command=self.analyze
        )
        self.analyze_button.pack(side="right")

        list_box = ttk.LabelFrame(
            self.window, text="References (same location / scene preferred)",
            padding=8,
        )
        list_box.pack(fill="x", padx=12, pady=(0, 7))
        self.ref_list = tk.Listbox(list_box, height=4, selectmode="extended")
        self.ref_list.pack(fill="x")

        region = ttk.LabelFrame(
            self.window, text="Suggested exposure shifts by overlapping region (EV)",
            padding=8,
        )
        region.pack(fill="both", padx=12, pady=7)
        self.heatmap = tk.Canvas(
            region, height=190, background="#17212e", highlightthickness=0
        )
        self.heatmap.pack(fill="x", expand=True)
        self.heatmap.bind("<Configure>", lambda _event: self.draw_heatmap())

        body = ttk.LabelFrame(self.window, text="Reference matching results", padding=8)
        body.pack(fill="both", expand=True, padx=12, pady=7)
        self.details = tk.Text(
            body, height=10, wrap="word", background="#101824",
            foreground="#dcece9", insertbackground="#dcece9",
            font=("Consolas", 10), padx=9, pady=9,
        )
        self.details.pack(fill="both", expand=True)
        self.details.insert(
            "1.0",
            "Add reference photos or individual video frames. "
            "Then click Analyze overlap.\n"
            "Tip: you can move the main video's timeline and add snapshots "
            "from several frames before analyzing.",
        )
        self.details.configure(state="disabled")

        footer = ttk.Frame(self.window, padding=(12, 4, 12, 12))
        footer.pack(fill="x")
        self.status = tk.StringVar(value="No reference images loaded.")
        ttk.Label(
            footer, textvariable=self.status, wraplength=690
        ).pack(side="left", fill="x", expand=True)
        self.apply_button = ttk.Button(
            footer, text="Apply global EV + RGB gains...",
            command=self.apply_suggestion, state="disabled"
        )
        self.apply_button.pack(side="right")
        self.window.after(120, self.poll_results)

    def _set_details(self, content: str) -> None:
        self.details.configure(state="normal")
        self.details.delete("1.0", "end")
        self.details.insert("1.0", content)
        self.details.configure(state="disabled")

    def _add(self, label: str, frame: np.ndarray) -> None:
        """Store a copy and invalidate the previous analysis."""
        self.references.append((label, prepare_reference(frame)))
        self.ref_list.insert("end", label)
        self.report = None
        self.apply_button.configure(state="disabled")
        self.status.set(f"{len(self.references)} reference(s) ready for analysis.")

    def add_photos(self) -> None:
        paths = filedialog.askopenfilenames(
            parent=self.window, title="Select reference photos",
            filetypes=IMAGE_TYPES,
        )
        for path in paths:
            frame = cv2.imread(path, cv2.IMREAD_COLOR)
            if frame is None:
                messagebox.showwarning(
                    "Reference image", "Cannot decode " + str(path),
                    parent=self.window
                )
                continue
            self._add(Path(path).name, frame)

    def add_current_frame(self) -> None:
        frame = self.source_provider()
        if frame is None:
            messagebox.showinfo(
                "Reference frame", "Open a photo or video first.",
                parent=self.window,
            )
            return
        self._add(self.source_label(), frame)

    def add_video_frame(self) -> None:
        path = filedialog.askopenfilename(
            parent=self.window, title="Select reference video",
            filetypes=VIDEO_TYPES,
        )
        if not path:
            return
        capture = cv2.VideoCapture(path)
        if not capture.isOpened():
            messagebox.showerror(
                "Reference video", "Could not open the video.",
                parent=self.window,
            )
            return
        try:
            length = max(1, int(capture.get(cv2.CAP_PROP_FRAME_COUNT)))
            index = simpledialog.askinteger(
                "Reference video frame",
                f"Select frame index from 0 to {length - 1}:",
                parent=self.window, minvalue=0, maxvalue=length - 1,
                initialvalue=0,
            )
            if index is None:
                return
            capture.set(cv2.CAP_PROP_POS_FRAMES, index)
            success, frame = capture.read()
            if not success:
                messagebox.showerror(
                    "Reference video", "Could not decode selected frame.",
                    parent=self.window,
                )
                return
            self._add(f"{Path(path).name} | frame {index}", frame)
        finally:
            capture.release()

    def remove_selected(self) -> None:
        indices = list(self.ref_list.curselection())
        if not indices:
            return
        for index in reversed(indices):
            del self.references[index]
            self.ref_list.delete(index)
        self.report = None
        self.apply_button.configure(state="disabled")
        self.status.set(f"{len(self.references)} reference(s) remaining.")
        self.draw_heatmap()

    def analyze(self) -> None:
        if self.busy:
            return
        source = self.source_provider()
        if source is None:
            messagebox.showinfo(
                "Reference analysis", "Open a source image or video first.",
                parent=self.window,
            )
            return
        if not self.references:
            messagebox.showinfo(
                "Reference analysis", "Add a reference image or frame first.",
                parent=self.window,
            )
            return
        references = list(self.references)
        self.busy = True
        self.report = None
        self.analyze_button.configure(state="disabled")
        self.apply_button.configure(state="disabled")
        self.status.set("Aligning reference images and measuring overlap...")
        self._set_details("Analyzing locally. No images are uploaded.")

        def worker() -> None:
            try:
                report = analyze_references(source, references)
                self.results.put(("complete", report))
            except Exception as error:
                self.results.put(("error", str(error)))

        threading.Thread(
            target=worker,
            name="PicVidFix reference comparison",
            daemon=True,
        ).start()

    def poll_results(self) -> None:
        if not self.window.winfo_exists():
            return
        try:
            while True:
                kind, payload = self.results.get_nowait()
                self.busy = False
                self.analyze_button.configure(state="normal")
                if kind == "error":
                    self.status.set("Reference analysis failed.")
                    self._set_details(str(payload))
                    continue
                self.report = payload
                self._set_details(
                    describe_reference(payload) + "\n\n" + format_report(payload)
                )
                matching = [
                    item for item in payload.references if item.matched
                ]
                self.status.set(
                    f"{len(matching)} of {len(payload.references)} overlap matches. "
                    "Inspect disagreements before applying corrections."
                )
                enable = (
                    bool(matching)
                    and payload.exposure_ev is not None
                    and payload.disagreement_ev is not None
                    and payload.disagreement_ev <= 0.4
                    and max(item.confidence for item in matching) >= 0.3
                )
                self.apply_button.configure(
                    state="normal" if enable else "disabled"
                )
                self.draw_heatmap()
        except queue.Empty:
            pass
        self.window.after(120, self.poll_results)

    def draw_heatmap(self) -> None:
        canvas = self.heatmap
        canvas.delete("all")
        width = max(1, canvas.winfo_width())
        height = max(1, canvas.winfo_height())
        if self.report is None:
            canvas.create_text(
                width / 2, height / 2,
                text="No analysis yet", fill="#abb9c9",
                font=("Segoe UI", 11),
            )
            return

        for row in range(GRID_ROWS):
            for col in range(GRID_COLUMNS):
                value = self.report.tile_ev[row][col]
                x0 = col * width / GRID_COLUMNS
                x1 = (col + 1) * width / GRID_COLUMNS
                y0 = row * height / GRID_ROWS
                y1 = (row + 1) * height / GRID_ROWS
                if value is None:
                    fill = "#27313c"
                    label = "?"
                elif value >= 0:
                    intensity = int(min(150, 30 + abs(value) * 60))
                    fill = f"#1b{65 + intensity:02x}5a"
                    label = f"{value:+.1f}"
                else:
                    intensity = int(min(140, 25 + abs(value) * 55))
                    fill = f"#{80 + intensity:02x}4b26"
                    label = f"{value:+.1f}"
                canvas.create_rectangle(
                    x0, y0, x1, y1,
                    fill=fill, outline="#465463", width=1,
                )
                canvas.create_text(
                    (x0 + x1) / 2, (y0 + y1) / 2, text=label,
                    fill="#f3f6fb", font=("Consolas", 11),
                )

    def apply_suggestion(self) -> None:
        """Explicit confirmation avoids silently replacing existing settings."""
        report = self.report
        if report is None or report.exposure_ev is None:
            return
        gains = report.balance_bgr or (1.0, 1.0, 1.0)
        permitted = messagebox.askyesno(
            "Apply reference suggestion?",
            "Replace your current global exposure and three RGB gain "
            "controls with this reference-based estimate?\n\n"
            f"Exposure: {report.exposure_ev:+.2f} EV\n"
            f"B / G / R: {gains[0]:.3f}, {gains[1]:.3f}, {gains[2]:.3f}\n\n"
            "The local heatmap is diagnostic only; it will NOT be applied. "
            "The original file is not changed.",
            parent=self.window,
        )
        if permitted:
            self.apply_callback(report)
            self.status.set("Global suggestion applied to the desktop controls.")
