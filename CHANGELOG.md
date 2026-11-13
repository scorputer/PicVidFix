# Change history

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

Historical GUIs are reference snapshots and are **not guaranteed to work** with the current root pipeline. To reproduce a specific historical version, use that version's original ZIP or match it with its contemporaneous dependencies. Video samples, complete original test suite, and original packaged releases have not yet been imported.

**Regression policy:** New image geometry, ROI, and numeric correction features should include targeted automated tests and CI coverage before releases. Full interactive GUI validation is an additional manual gate.
