# HueSatVal v1.5

Current version of PicVidFix.

## Features
- Side-by-side original and corrected preview
- Still images and video with editable numeric sliders
- Selectable fixed-region subject-priority automatic exposure
- Reset all and per-control reset
- Saved correction presets
- Background MP4 export with original audio and cancellation

## Setup
```powershell
py -m pip install -r requirements.txt
py video_color_gui.py
```

Video export needs FFmpeg installed and accessible on PATH. The single-file alternative is `HueSatVal_v1_5_standalone.py`.

The dynamic subject region remains fixed in the picture. Moving-object tracking is not implemented. Preview playback is silent.
