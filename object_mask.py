"""Non-destructive, object-local color corrections for PicVidFix.

OpenCV GrabCut segments a selected object from a rough rectangle; users can
refine the resulting mask with foreground/background brush strokes.
The same mask and settings can be applied by preview and still/video export.

Masks are uint8, 0 (background) through 255 (object), at any resolution.
On export they are resized to the native frame resolution.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass(frozen=True)
class ObjectCorrectionSettings:
    """A reusable correction applied only through the selected object mask."""

    enabled: bool = False
    exposure_ev: float = 0.0
    saturation: float = 1.0
    strength: float = 1.0
    feather_px: float = 4.0


def mask_applies_to_frame(
    *,
    is_video: bool,
    current_frame: int,
    selected_frame: int | None,
    fixed_for_video: bool,
) -> bool:
    """Do not apply a selected video object's mask to unrelated frames.

    Without explicit fixed-mask opt-in, only the source frame where the object
    was selected may display its correction.
    """
    if not is_video:
        return True
    return bool(fixed_for_video or current_frame == selected_frame)


def segment_object(
    image: np.ndarray,
    bounds: tuple[int, int, int, int],
    iterations: int = 5,
) -> np.ndarray:
    """Return a GrabCut mask seeded by a top-left x/y/width/height rectangle.

    The image should be an analysis-sized (rather than full 5K) BGR frame.
    Reject selections that are too small, full-image, or have no foreground.
    """
    if image.dtype != np.uint8 or image.ndim != 3 or image.shape[2] != 3:
        raise ValueError("Object selection requires an 8-bit BGR image.")
    height, width = image.shape[:2]
    x, y, box_width, box_height = [int(v) for v in bounds]
    if not 1 <= iterations <= 12:
        raise ValueError("GrabCut iteration count must be between 1 and 12.")
    if box_width < 4 or box_height < 4:
        raise ValueError("Draw a rectangle at least four pixels wide and tall.")
    if x < 1 or y < 1 or x + box_width >= width or y + box_height >= height:
        raise ValueError(
            "Leave a little background outside the object rectangle."
        )
    model_bg = np.zeros((1, 65), dtype=np.float64)
    model_fg = np.zeros((1, 65), dtype=np.float64)
    gc_mask = np.zeros((height, width), dtype=np.uint8)
    try:
        cv2.grabCut(
            image, gc_mask, (x, y, box_width, box_height),
            model_bg, model_fg, iterations, cv2.GC_INIT_WITH_RECT,
        )
    except cv2.error as error:
        raise ValueError(
            "Could not isolate an object. Try a different rectangle."
        ) from error
    foreground = np.isin(gc_mask, (cv2.GC_FGD, cv2.GC_PR_FGD))
    if np.count_nonzero(foreground) < 8:
        raise ValueError(
            "No foreground found. Draw closer to the object, "
            "leaving contrasting background visible."
        )
    return (foreground.astype(np.uint8) * 255)


def paint_object_mask(
    mask: np.ndarray,
    x: int,
    y: int,
    radius: int,
    foreground: bool,
) -> np.ndarray:
    """Return a copy with a circular keep/remove brush stroke applied."""
    if mask.ndim != 2 or mask.dtype != np.uint8:
        raise ValueError("Selection mask must be a uint8 grayscale array.")
    if not 1 <= radius <= 200:
        raise ValueError("Brush radius must be between 1 and 200 pixels.")
    result = mask.copy()
    cv2.circle(
        result, (int(x), int(y)), radius,
        255 if foreground else 0, thickness=-1,
    )
    return result


def apply_object_correction(
    frame: np.ndarray,
    mask: np.ndarray,
    settings: ObjectCorrectionSettings,
) -> np.ndarray:
    """Apply local exposure and saturation, preserving background and alpha.

    This operation does not mutate either input. Feathering occurs at native
    image dimensions, preserving smooth edges in full-resolution exports.
    """
    if frame.dtype != np.uint8 or frame.ndim != 3 or frame.shape[2] not in (3, 4):
        raise ValueError("Object correction requires an 8-bit BGR/BGRA frame.")
    if mask.ndim != 2 or mask.dtype != np.uint8 or not mask.size:
        raise ValueError("Object correction requires a nonempty uint8 mask.")
    if not (-3 <= settings.exposure_ev <= 3):
        raise ValueError("Local exposure must be between -3 and +3 EV.")
    if not (0 <= settings.saturation <= 3):
        raise ValueError("Local saturation must be between 0 and 3.")
    if not (0 <= settings.strength <= 1):
        raise ValueError("Object correction strength must be in [0, 1].")
    if not (0 <= settings.feather_px <= 100):
        raise ValueError("Object feather must be between 0 and 100 pixels.")

    if (
        not settings.enabled
        or settings.strength == 0
        or (settings.exposure_ev == 0 and settings.saturation == 1)
    ):
        return frame.copy()

    height, width = frame.shape[:2]
    if mask.shape != (height, width):
        mask = cv2.resize(mask, (width, height), interpolation=cv2.INTER_LINEAR)
    weights = mask.astype(np.float32) / 255.0
    if settings.feather_px:
        weights = cv2.GaussianBlur(
            weights, (0, 0), sigmaX=settings.feather_px,
            sigmaY=settings.feather_px,
        )
    weights = np.clip(weights * settings.strength, 0.0, 1.0)
    if not np.any(weights):
        return frame.copy()

    color = frame[:, :, :3]
    hsv = cv2.cvtColor(color, cv2.COLOR_BGR2HSV).astype(np.float32)
    hsv[:, :, 1] = np.clip(hsv[:, :, 1] * settings.saturation, 0, 255)
    hsv[:, :, 2] = np.clip(
        hsv[:, :, 2] * (2.0 ** settings.exposure_ev), 0, 255
    )
    transformed = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)
    result_bgr = np.clip(
        color.astype(np.float32) * (1.0 - weights[:, :, None])
        + transformed.astype(np.float32) * weights[:, :, None],
        0, 255,
    ).astype(np.uint8)
    if frame.shape[2] == 4:
        return np.dstack((result_bgr, frame[:, :, 3].copy()))
    return result_bgr
