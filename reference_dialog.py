"""Multi-reference color analysis workbench for PicVidFix Desktop.

Show source and selected target images side-by-side. Both video panes have
independent frame sliders, exact numeric frame entry, and visible 0-based
frame counts. Analysis remains advisory until the user explicitly applies
the optional global recommendation.
"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
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
from reference_preview import FramePreviewPanel, frame_caption

IMAGE_TYPES = (
    ("Photo files", "*.jpg *.jpeg *.png *.tif *.tiff *.bmp *.webp"),
    ("All files", "*.*"),
)
VIDEO_TYPES = (
    ("Videos", "*.mp4 *.mov *.avi *.mkv *.webm *.m4v"),
    ("All files", "*.*"),
)


def prepare_reference(frame: np.ndarray) -> np.ndarray:
    """Limit analysis-cache RAM without overwriting an original image."""
    height, width = frame.shape[:2]
    scale = min(1.0, MAX_ANALYSIS_SIDE / max(height, width))
    if scale >= 1.0:
        return frame.copy()
    return cv2.resize(
        frame, (round(width * scale), round(height * scale)),
        interpolation=cv2.INTER_AREA,
    )


def describe_reference(report: ReferenceReport) -> str:
    """Explain positive/negative EV and the limits of match confidence."""
    successful = sum(item.matched for item in report.references)
    return (
        f"{successful} of {len(report.references)} reference(s) aligned. "
        "A +EV tile suggests brightening the source; a -EV tile suggests "
        "darkening it. Blank tiles were not supported by matched pixels. "
        "Alignment confidence is a heuristic, not a calibrated probability."
    )


@dataclass
class ReferenceItem:
    """Editable video-frame or static image entry in the comparison list."""

    label: str
    frame: np.ndarray
    video_path: str | None = None
    frame_index: int | None = None
    frame_count: int | None = None

    def display_label(self) -> str:
        return frame_caption(self.label, self.frame_index, self.frame_count)


class ReferenceMatchDialog:
    """Collect, preview, scrub and compare multiple reference images or frames."""

    def __init__(
        self,
        parent: tk.Misc,
        source_provider: Callable[[], np.ndarray | None],
        source_label: Callable[[], str],
        apply_callback: Callable[[ReferenceReport], None],
        source_video_info: Callable[[], tuple[str, int, int] | None] | None = None,
    ) -> None:
        self.source_provider = source_provider
        self.source_label = source_label
        self.apply_callback = apply_callback
        self.source_video_info = source_video_info
        self.references: list[ReferenceItem] = []
        self.results: queue.Queue = queue.Queue()
        self.report: ReferenceReport | None = None
        self.busy = False
        self.revision = 0

        self.window = tk.Toplevel(parent)
        self.window.title("PicVidFix | Reference color analysis")
        self.window.geometry("1050x930")
        self.window.minsize(780, 650)
        self.window.protocol("WM_DELETE_WINDOW", self.close)

        header = ttk.Frame(self.window, padding=10)
        header.pack(fill="x")
        ttk.Label(
            header,
            text="Compare source and reference images. For video, scrub each "
            "preview independently or enter an exact frame number.",
            wraplength=980,
        ).pack(anchor="w")
        ttk.Label(
            header,
            text="Only reliable same-scene overlaps are evaluated. "
            "Recommendations do not change the image until approved.",
            wraplength=980,
            foreground="#666666",
        ).pack(anchor="w", pady=(3, 0))

        commands = ttk.Frame(self.window, padding=(10, 4, 10, 7))
        commands.pack(fill="x")
        ttk.Button(
            commands, text="+ Photo(s)", command=self.add_photos
        ).pack(side="left", padx=(0, 5))
        ttk.Button(
            commands, text="+ Video", command=self.add_video_frame
        ).pack(side="left", padx=(0, 5))
        ttk.Button(
            commands, text="+ Source snapshot", command=self.add_current_frame
        ).pack(side="left", padx=(0, 5))
        ttk.Button(
            commands, text="Remove selected", command=self.remove_selected
        ).pack(side="left", padx=(0, 5))
        self.analyze_button = ttk.Button(
            commands, text="Analyze overlap", command=self.analyze
        )
        self.analyze_button.pack(side="right")

        previews = ttk.Frame(self.window, padding=(10, 0, 10, 6))
        previews.pack(fill="both", expand=False)
        self.source_panel = FramePreviewPanel(
            previews, "Source (current comparison)", self._source_changed
        )
        self.source_panel.pack(side="left", fill="both", expand=True, padx=(0, 5))
        self.reference_panel = FramePreviewPanel(
            previews, "Reference (selected below)", self._reference_changed
        )
        self.reference_panel.pack(side="left", fill="both", expand=True, padx=(5, 0))
        self._load_source()

        list_box = ttk.LabelFrame(
            self.window, text="Reference images and video frame numbers",
            padding=6,
        )
        list_box.pack(fill="x", padx=10, pady=(0, 6))
        self.ref_list = tk.Listbox(
            list_box, height=4, selectmode="extended", exportselection=False
        )
        self.ref_list.pack(fill="x")
        self.ref_list.bind("<<ListboxSelect>>", self._selected_reference)

        region = ttk.LabelFrame(
            self.window, text="Matched overlap: suggested regional exposure (EV)",
            padding=6,
        )
        region.pack(fill="both", padx=10, pady=(0, 6))
        self.heatmap = tk.Canvas(
            region, height=135, background="#17212e", highlightthickness=0
        )
        self.heatmap.pack(fill="x", expand=True)
        self.heatmap.bind("<Configure>", lambda _event: self.draw_heatmap())

        detail_box = ttk.LabelFrame(
            self.window, text="Alignment, confidence and lighting estimates",
            padding=6,
        )
        detail_box.pack(fill="both", expand=True, padx=10, pady=(0, 6))
        self.details = tk.Text(
            detail_box, height=8, wrap="word", background="#101824",
            foreground="#dcece9", insertbackground="#dcece9",
            font=("Consolas", 10), padx=9, pady=9,
        )
        self.details.pack(fill="both", expand=True)
        self._set_details(
            "Choose one or more reference images or videos. "
            "Select any list item to preview it. "
            "Video sliders and frame entry fields choose exact comparison frames."
        )

        footer = ttk.Frame(self.window, padding=(10, 5, 10, 10))
        footer.pack(fill="x")
        self.status = tk.StringVar(value="Choose a reference to begin.")
        ttk.Label(
            footer, textvariable=self.status, wraplength=650
        ).pack(side="left", fill="x", expand=True)
        self.apply_button = ttk.Button(
            footer, text="Apply global EV + B/G/R...",
            command=self.apply_suggestion, state="disabled"
        )
        self.apply_button.pack(side="right")
        self.window.after(130, self.poll_results)

    def _load_source(self) -> None:
        frame = self.source_provider()
        if frame is None:
            return
        information = (
            self.source_video_info() if self.source_video_info else None
        )
        if information:
            path, index, count = information
            self.source_panel.set_frame(
                frame, Path(path).name, video_path=path,
                frame_index=index, frame_count=count,
            )
        else:
            self.source_panel.set_frame(frame, self.source_label())

    def _invalidate(self) -> None:
        """Prevent an in-flight analysis from attaching to changed frames."""
        self.revision += 1
        self.report = None
        self.apply_button.configure(state="disabled")
        self.draw_heatmap()

    def _source_changed(self, _index: int, _frame: np.ndarray) -> None:
        self._invalidate()
        self.status.set("Source frame changed. Analyze again to compare it.")

    def _reference_changed(self, index: int, frame: np.ndarray) -> None:
        selected = self.ref_list.curselection()
        if not selected:
            return
        item = self.references[selected[0]]
        item.frame = prepare_reference(frame)
        item.frame_index = index
        self.ref_list.delete(selected[0])
        self.ref_list.insert(selected[0], item.display_label())
        self.ref_list.selection_set(selected[0])
        self._invalidate()
        self.status.set("Reference frame changed. Analyze again for new results.")

    def _set_details(self, content: str) -> None:
        self.details.configure(state="normal")
        self.details.delete("1.0", "end")
        self.details.insert("1.0", content)
        self.details.configure(state="disabled")

    def _add(
        self, label: str, frame: np.ndarray,
        *,
        video_path: str | None = None,
        frame_index: int | None = None,
        frame_count: int | None = None,
    ) -> None:
        item = ReferenceItem(
            label=label, frame=prepare_reference(frame),
            video_path=video_path, frame_index=frame_index,
            frame_count=frame_count,
        )
        self.references.append(item)
        index = len(self.references) - 1
        self.ref_list.insert("end", item.display_label())
        self.ref_list.selection_clear(0, "end")
        self.ref_list.selection_set(index)
        self.ref_list.activate(index)
        self._show_reference(index)
        self._invalidate()
        self.status.set(f"{len(self.references)} reference(s) ready for analysis.")

    def _show_reference(self, index: int) -> None:
        item = self.references[index]
        self.reference_panel.set_frame(
            item.frame, item.label,
            video_path=item.video_path,
            frame_index=item.frame_index,
            frame_count=item.frame_count,
        )

    def _selected_reference(self, _event=None) -> None:
        selected = self.ref_list.curselection()
        if selected:
            self._show_reference(selected[0])
        else:
            self.reference_panel.clear()

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
                    parent=self.window,
                )
                continue
            self._add(Path(path).name, frame)

    def add_current_frame(self) -> None:
        frame = self.source_panel.selected_frame()
        if frame is None:
            messagebox.showinfo(
                "Reference frame", "Open a source photo or video first.",
                parent=self.window,
            )
            return
        pane = self.source_panel
        self._add(
            pane.filename, frame,
            video_path=pane.video_path, frame_index=pane.frame_index,
            frame_count=pane.frame_count,
        )

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
                "Reference video", "Could not open video.", parent=self.window
            )
            return
        try:
            count = max(1, int(capture.get(cv2.CAP_PROP_FRAME_COUNT)))
            success, frame = capture.read()
            if not success:
                messagebox.showerror(
                    "Reference video", "Could not decode frame 0.",
                    parent=self.window,
                )
                return
            self._add(
                Path(path).name, frame, video_path=path,
                frame_index=0, frame_count=count,
            )
        finally:
            capture.release()

    def remove_selected(self) -> None:
        selected = self.ref_list.curselection()
        if not selected:
            return
        for index in reversed(selected):
            del self.references[index]
            self.ref_list.delete(index)
        self.ref_list.selection_clear(0, "end")
        if self.references:
            index = min(selected[0], len(self.references) - 1)
            self.ref_list.selection_set(index)
            self._show_reference(index)
        else:
            self.reference_panel.clear()
        self._invalidate()
        self.status.set(f"{len(self.references)} reference(s) remaining.")

    def analyze(self) -> None:
        if self.busy:
            return
        source = self.source_panel.selected_frame()
        if source is None or not self.references:
            messagebox.showinfo(
                "Reference analysis",
                "Open a source and select at least one reference.",
                parent=self.window,
            )
            return
        references = [
            (item.display_label(), item.frame.copy())
            for item in self.references
        ]
        self.busy = True
        self._invalidate()
        revision = self.revision
        self.analyze_button.configure(state="disabled")
        self.status.set("Aligning selected frames and measuring overlap...")
        self._set_details("Working locally. No images are uploaded.")

        def worker() -> None:
            try:
                report = analyze_references(source, references)
                self.results.put(("complete", revision, report))
            except Exception as error:
                self.results.put(("error", revision, str(error)))

        threading.Thread(
            target=worker, name="PicVidFix reference analysis", daemon=True
        ).start()

    def poll_results(self) -> None:
        if not self.window.winfo_exists():
            return
        try:
            while True:
                kind, revision, payload = self.results.get_nowait()
                self.busy = False
                self.analyze_button.configure(state="normal")
                if revision != self.revision:
                    self.status.set(
                        "Frames changed while analyzing. Analyze again."
                    )
                    continue
                if kind == "error":
                    self.status.set("Reference comparison failed.")
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
                    f"{len(matching)}/{len(payload.references)} reliable overlaps. "
                    "Inspect disagreement before applying global corrections."
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
        self.window.after(130, self.poll_results)

    def draw_heatmap(self) -> None:
        canvas = self.heatmap
        canvas.delete("all")
        width = max(1, canvas.winfo_width())
        height = max(1, canvas.winfo_height())
        if self.report is None:
            canvas.create_text(
                width / 2, height / 2, text="No analysis yet",
                fill="#abb9c9", font=("Segoe UI", 11),
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
                    fill, label = "#27313c", "?"
                elif value >= 0:
                    intensity = int(min(150, 30 + abs(value) * 60))
                    fill, label = f"#1b{65 + intensity:02x}5a", f"{value:+.1f}"
                else:
                    intensity = int(min(140, 25 + abs(value) * 55))
                    fill, label = f"#{80 + intensity:02x}4b26", f"{value:+.1f}"
                canvas.create_rectangle(
                    x0, y0, x1, y1, fill=fill,
                    outline="#465463", width=1,
                )
                canvas.create_text(
                    (x0 + x1) / 2, (y0 + y1) / 2,
                    text=label, fill="#f3f6fb",
                    font=("Consolas", 11),
                )

    def apply_suggestion(self) -> None:
        report = self.report
        if report is None or report.exposure_ev is None:
            return
        gains = report.balance_bgr or (1.0, 1.0, 1.0)
        permitted = messagebox.askyesno(
            "Apply reference suggestion?",
            "Replace your global exposure and B/G/R gain controls?\n\n"
            f"Exposure: {report.exposure_ev:+.2f} EV\n"
            f"B/G/R: {gains[0]:.3f}, {gains[1]:.3f}, {gains[2]:.3f}\n\n"
            "The local heatmap is diagnostic and will NOT be applied. "
            "Your original file is unchanged.",
            parent=self.window,
        )
        if permitted:
            self.apply_callback(report)
            self.status.set("Global suggestion applied. Regional map is advisory.")

    def close(self) -> None:
        self.source_panel.clear()
        self.reference_panel.clear()
        self.window.destroy()
