# Change history

## Desktop v1.7: select an object and correct only its pixels

- Added **Select object...** in the main desktop toolbar, with a zoomable
  image, rough rectangle seed, GrabCut automatic separation and manual
  Keep/Remove mask brushes
- Object-specific exposure, saturation, mask strength and feather controls
  use the common image processing wrapper for preview and still/video exports
- Preview-only object outlines do not alter saved pixels
- Stationary video masks are applied across the clip only after explicit
  opt-in and confirmation; no moving-object tracking is claimed
- Resets, media changes, and presets handle object masks separately from
  reusable correction parameters
- Added deterministic automated tests covering segmentation, boundaries,
  mask compositing, alpha, large source coordinates and preview/save parity

## Desktop v1.6: frame-aware comparisons and virtual ring lighting

- Side-by-side source/target previews in the multi-reference comparison panel,
  with video frame slider and editable exact frame index in both panes
- Frame numbers in the main original and corrected video previews, reference
  list and previews, video ROI inspector, and metering/calibration popups
- Signed virtual ring-light exposure correction with normalized center,
  adjustable radius/feathering, and optional center-vs-edge EV estimation
- Shared color pipeline keeps ring corrections consistent for preview,
  still-image save, and MP4 export; presets save the ring controls
- Additional automated regression tests for video indexes, preview scaling,
  ring falloff, image immutability, alpha, highlighted pixels and metering

## Reference-guided color analysis (desktop preview)

- Added a multi-reference comparison panel: multiple still photos, selected
  frames from video files, or snapshots from the current clip
- ORB/RANSAC registration rejects untrusted overlaps; only supported pixels
  contribute to an EV map and global chromatic-balance suggestions
- Weighted multi-reference consensus flags substantial exposure disagreement
  and leaves unmatched grid cells unknown
- User-confirmed application may update global exposure and RGB gains;
  spatial suggestions are *diagnostics only*, not blended into export yet
- New automated regression tests for alignment, false matches, consensus,
  large-reference image preparation and non-destructive analysis

## Web releases

| Version | Change |
| --- | --- |
| Web v0.1 | Static browser image corrections, comparisons, presets and PNG export |
| Web v0.2 | Geometry straightening, perspective, flips, draggable linear/radial gradients with regional exposure and saturation, reset controls, and added tests |
| Web v0.2.1 | Hover/touch X/Y native-resolution output-canvas pixel coordinates with HUD, footer readout, and mapping regression tests |
| Web v0.3 | Fit, 100%, incremental zoom, cursor-based wheel zoom, scrolling/panning, and adaptive high-detail preview |
| Web v0.3.1 | Persistent drag-to-select bounding box overlay, X/Y/W/H readout, downloadable machine-readable ROI JSON, and regression tests |
| Web v0.3.2 | Display all four inclusive ROI corner coordinates with regression tests |


The original project is based on `HueSatVal3.py` (preserved in `baseline/`). Version labels below refer to the project ZIPs developed during the conversation, not reconstructed Git tags.

| Version | Change | GitHub source availability |
| --- | --- | --- |
| v1.0 | First video GUI, shared color pipeline, presets and MP4 export | Original baseline only; complete v1.0 snapshot not yet imported |
| v1.1 | Reliable FFmpeg export, cancellation and status | Historical GUI and shared pre-v1.5 pipeline |
| v1.2 | Editable numeric values, image open/save mode | Original ZIP exists outside this repo; source snapshot not yet imported |
| v1.3 | Full-resolution preview consistency, background preview processing | Historical GUI and shared pre-v1.5 pipeline |
| v1.4 | Reset all and per-control reset | Historical GUI and shared pre-v1.5 pipeline |
| v1.5 | Fixed-region, smoothed subject-priority auto-exposure | Modular and standalone source at root |
| v1.5.1 | Main-preview X/Y cursor display, zoomable/pannable ROI inspector popup, non-destructive box overlays and JSON export | Current modular desktop GUI + region_inspector.py; standalone remains v1.5 |
| v1.5.2 | Multi-reference overlap/EV analysis for still images and video frames; optional global suggestion, regional diagnostic heatmap and tests | Current modular desktop GUI + reference_match.py + reference_dialog.py; standalone remains v1.5 |
| v1.6 | Independent video source/reference frame scrubbing, visible frame numbering, preview panes and signed radial light-ring correction | Modular desktop source; standalone remains v1.5 |
| v1.7 | Object selection with GrabCut, editable mask brushes and per-object corrections, plus explicit fixed-video opt-in | Modular desktop GUI + object_mask.py + object_dialog.py; standalone remains v1.5 |

Historical GUIs are reference snapshots and are **not guaranteed to work** with the current root pipeline. To reproduce a specific historical version, use that version's original ZIP or match it with its contemporaneous dependencies. Video samples, complete original test suite, and original packaged releases have not yet been imported.

**Regression policy:** New image geometry, ROI, and numeric correction features should include targeted automated tests and CI coverage before releases. Full interactive GUI validation is an additional manual gate.
