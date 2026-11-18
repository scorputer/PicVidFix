# PicVidFix

Free, privacy-friendly photo and video correction tools, starting with the
original Python `HueSatVal3.py` engine.

## Choose an edition

| Edition | Status | How to run |
| --- | --- | --- |
| **Web v0.3.2** | Browser-based photo editor with zoom/pan and analysis ROIs (no server uploads) | Open `web/` through a static HTTP server, or deploy it to Cloudflare Pages |
| **Desktop v1.7** | Python image/video editor with dynamic subject-region exposure and region inspector | `py video_color_gui.py` |
| **Standalone v1.5** | Same desktop app in a single Python file | `py HueSatVal_v1_5_standalone.py` |

## Web quick start

The web application requires no package installation or backend. From this
repository's root directory:

```powershell
py -m http.server 8787
```

Open **http://localhost:8787/web/** in your browser.

Web v0.3.2 supports photo loading, precise controls, correction presets,
neutral-patch white balance, before/after comparison, and PNG export.
It now includes geometric straightening/perspective and editable graduated
foreground/background or radial exposure masks.
A live pixel-position readout reports native-resolution X/Y coordinates while
hovering or tapping the preview. Zoom/pan controls let you inspect a 5K image
without reducing its stored resolution. **Draw box** selects a persistent,
non-destructive rectangle with X/Y/W/H and four-corner coordinates,
plus downloadable ROI JSON.
Preview and export use the same JavaScript processing function. For speed, the
preview runs at a reduced resolution while export runs at full resolution,
so fine detail and edge feathering can differ slightly. The current web
edition does **not** process video yet; use the desktop application.

**Deploy on Cloudflare Pages for free:** Connect this GitHub repository, choose
`main`, select no framework, leave the build command blank, and set
the output directory to **`web`**. See [web/README.md](web/README.md) for
full details. Cloudflare account setup is user-operated.

## Desktop quick start (Windows / Python 3.10+)

```powershell
py -m pip install -r requirements.txt
py video_color_gui.py
```

MP4 export requires a separate FFmpeg installation with `ffmpeg.exe` on PATH.

### Object-specific corrections (desktop v1.7)

Select **Select object...** in the main toolbar, or **Select / refine
object...** in the scrolling right-hand panel. In the object selection window:

1. Draw a rough rectangle around the subject, leaving some surrounding
   background. Click **Find object** to use OpenCV GrabCut segmentation.
2. Inspect the cyan/teal highlighted mask. Select **Keep brush** or
   **Remove brush** and paint over errors. The mouse wheel zooms; hold
   **Space and drag** to pan.
3. Click **Use selection**. Adjust **Object exposure**, **Object saturation**,
   **Mask strength** and **Edge feather** in the main right-hand panel.

The object mask is **non-destructive**. The selected boundary is drawn on
both main previews as a guide but is not embedded into the saved file. Still
image exports use the same masked full-resolution correction as previews.
GrabCut and brush work on a <=1200-pixel-wide analysis copy and the mask
is resampled with feathering during full-resolution processing. This keeps
large images responsive but means extremely fine object edges are approximate.

**Video:** A newly selected object is initially corrected only on the
frame where it was selected. To apply the same mask to every frame, check
**Use FIXED mask across video (no motion tracking)**; video export asks
for explicit confirmation. A fixed-position mask is useful for a stationary
camera/subject but will NOT follow a person or object as it moves. If
fixed-mask mode is off, the export can proceed only after acknowledging
that object correction will be excluded. Automatic object tracking is a
future development step.

Object correction sliders are saved in presets, but the source-specific
pixel mask is NOT embedded in the JSON preset. Draw a new object mask when
changing photos or videos. The older standalone v1.5 and web editor do
not yet include this object-selection workflow.

The desktop **Color references...** panel now shows source and selected
reference side by side. When either pane displays video, it has a frame slider,
an exact numeric frame-index field, and visible zero-based frame numbering.
Both panes navigate independently; the **Refresh source** button reloads the
main editor's latest selected source frame if it changed while the comparison
panel was open. The main source/corrected previews also show their current
video frame number.

The **Virtual ring light / anti-hotspot** controls add a signed, feathered
radial EV correction to either image or video: positive values add central
fill light, negative values reduce central glare. Position, radius and
feather are adjustable. **Estimate center-to-edge lighting** suggests an EV
using medians from the source frame, but content can bias that meter; verify
the preview. **Show ring guide in preview only** marks its center and radius
without writing the guide into saved media. Highlight protection reduces additional brightening of near-white
areas but cannot recover previously clipped data. This ring effect is part
of the common Python image processing pipeline, so preview, still save and
video export use it consistently. Ring settings are included in presets.

The desktop **Compare photos / video frames...** panel accepts multiple
reference photos and individual video frames. It uses ORB feature matching
and RANSAC to detect overlapping content, estimates consensus exposure and
relative B/G/R balance, and displays a 6×8 map of suggested regional EV
shifts. You can explicitly accept the *global* estimate; local shifts are
analysis-only until spatial application is implemented. Unmatched references
never trigger an automatic correction. For now this workflow requires
approximately the same scene, not unrelated cinematic style references.

The desktop app retains manual color adjustment, image mode, video mode,
subject-priority exposure analysis over a fixed frame region, presets and
MP4 export. **Inspect / Draw box** opens a zoomable, panning ROI inspector
that can export pixel-precise rectangle coordinates as JSON. Hovering over
either main preview now displays full-resolution cursor coordinates.
Double-click either main preview, or use **Zoom / Inspect / Box**, to open
the zoomable desktop region inspector. The main panels are fit-to-window. Preview playback is silent. Variable-frame-rate and HDR handling
are outside its current scope.

## Project layout

- `web/`: Browser photo editor, static assets, Node unit tests and deployment docs
- `video_color_gui.py`: Current Python GUI
- `color_pipeline.py`: Shared Python correction engine
- `region_inspector.py`: Dedicated desktop zoom/pan and ROI annotation tool
- `reference_match.py`: Robust overlap detection and multi-reference EV/color comparisons
- `reference_dialog.py`: Desktop panel to collect photos/frames and review regional suggestions
- `reference_preview.py`: Side-by-side image/video previews with frame navigation
- `light_ring.py`: Radial exposure field and optional center/edge metering
- `object_mask.py`: GrabCut segmentation, manual brush editing and local pixel correction
- `object_dialog.py`: Zoomable object picker and foreground/background mask refinement
- `tests/test_object_mask.py`: Object-selection, static-video and correction-parity regressions
- `HueSatVal_v1_5_standalone.py`: Historical all-in-one v1.5 desktop version
  (does **not** yet include the new region inspector)
- `baseline/`: Original unmodified `HueSatVal3.py`
- `versions/`: Historical GUI source snapshots
- `CHANGELOG.md`: Version history and known recovery gaps
- `.github/workflows/`: Desktop smoke tests and web checks

## Testing

Web engine and syntax checks require Node.js 20+:

```powershell
cd web
npm run check
npm test
```

Desktop test requirements:

```powershell
py -m unittest discover -s tests -v
```

## Project principles

1. No mandatory subscriptions or usage charges.
2. The editor never uploads the user's images to a processing server.
3. Manual adjustment precision and reproducible exports matter.
4. Large or unsupported media should gracefully point to the desktop edition.
5. Preserve the original working Python source as the reference implementation.

This is experimental software and may have browser/OS-specific behavior.
