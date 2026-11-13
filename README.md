# PicVidFix

Free, privacy-friendly photo and video correction tools, starting with the
original Python `HueSatVal3.py` engine.

## Choose an edition

| Edition | Status | How to run |
| --- | --- | --- |
| **Web v0.3.2** | Browser-based photo editor with zoom/pan and analysis ROIs (no server uploads) | Open `web/` through a static HTTP server, or deploy it to Cloudflare Pages |
| **Desktop v1.5.1** | Python image/video editor with dynamic subject-region exposure and region inspector | `py video_color_gui.py` |
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
