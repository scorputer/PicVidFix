# PicVidFix

Photo and video color-correction tools built around the HueSatVal pipeline.

The current working version is **v1.5**, with live image/video previews, manual color adjustments, fixed-region subject-priority auto-exposure, presets, and FFmpeg MP4 export.

## Quick start (Windows)

```powershell
py -m pip install -r requirements.txt
py video_color_gui.py
```

Install FFmpeg separately and ensure `ffmpeg.exe` is on your PATH for MP4 export. For the single-file version, run `py HueSatVal_v1_5_standalone.py`.

Historical source snapshots will be organized in `versions/`. The original `HueSatVal3.py` is retained in `baseline/`.

This is an experimental project. Preview playback is silent and exported video uses constant nominal FPS; HDR and variable-frame-rate media need additional handling.
