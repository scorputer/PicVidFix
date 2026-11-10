"""Reusable still-image and video-frame color corrections.

Adapted from HueSatVal3.py. A frame is a NumPy array in OpenCV's BGR/BGRA
format. This module intentionally does not load or save files.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np


@dataclass(frozen=True)
class ColorSettings:
    """All controls from the original HueSatVal3.py script."""

    coords: Optional[tuple[int, int, int, int]] = None
    hex: str = "FFFFFF"
    offset: Optional[tuple[float, float, float]] = None  # Blue, green, red
    remap_blue: Optional[str] = None
    blue_range: tuple[int, int] = (95, 135)
    brightness: float = 0.0
    contrast: float = 1.0
    exposure: float = 0.0
    saturation: float = 1.0
    vibrance: float = 0.0
    highlights: float = 0.0
    shadows: float = 0.0
    gamma: float = 1.0
    sharpness: float = 0.0


def hex_to_bgr(hex_str: str) -> np.ndarray:
    """Convert a six-digit RGB hex color into OpenCV BGR floating values."""
    value = hex_str.lstrip("#")
    if len(value) != 6 or any(ch not in "0123456789abcdefABCDEF" for ch in value):
        raise ValueError(f"Invalid RGB hex color: {hex_str!r}. Use RRGGBB.")
    return np.array(
        [int(value[4:6], 16), int(value[2:4], 16), int(value[0:2], 16)],
        dtype=np.float32,
    )


def adjust_gamma(image: np.ndarray, gamma: float = 1.0) -> np.ndarray:
    """Use the original gamma convention: values above one brighten."""
    if gamma <= 0:
        raise ValueError("Gamma must be greater than zero.")
    inv_gamma = 1.0 / gamma
    table = np.array(
        [((index / 255.0) ** inv_gamma) * 255 for index in range(256)]
    ).astype(np.uint8)
    return cv2.LUT(image, table)


def measure_balance_offset(
    image: np.ndarray,
    coords: tuple[int, int, int, int],
    target_hex: str = "FFFFFF",
) -> tuple[float, float, float]:
    """Sample a patch and return reusable blue/green/red gains.

    For video, sample one representative frame, then keep the returned offset
    constant for the whole clip to prevent frame-to-frame color pumping.
    """
    if image.ndim == 2:
        bgr = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    elif image.ndim == 3 and image.shape[2] in (3, 4):
        bgr = image[:, :, :3]
    else:
        raise ValueError("Expected an 8-bit gray, BGR, or BGRA image.")

    height, width = bgr.shape[:2]
    x1, y1, x2, y2 = coords
    x1, x2 = sorted((max(0, min(width, x1)), max(0, min(width, x2))))
    y1, y2 = sorted((max(0, min(height, y1)), max(0, min(height, y2))))
    if x2 <= x1 or y2 <= y1:
        raise ValueError("Calibration rectangle must contain at least one pixel.")

    measured = np.mean(bgr[y1:y2, x1:x2], axis=(0, 1))
    measured = np.clip(measured, 1.0, 255.0)
    goal = hex_to_bgr(target_hex)
    gains = goal / measured
    return tuple(float(gain) for gain in gains)


def process_frame(frame: np.ndarray, settings: ColorSettings) -> np.ndarray:
    """Apply the original correction stages in their original order.

    Args:
        frame: uint8 grayscale, BGR, or BGRA frame, unmodified by this call.
        settings: Corrections to apply. Use static offsets for video.

    Returns:
        A new uint8 image. BGRA alpha is preserved unchanged.
    """
    if frame.dtype != np.uint8:
        raise ValueError("Only uint8 video/image frames are supported.")
    if frame.ndim == 2:
        bgr = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR).astype(np.float32)
        alpha = None
    elif frame.ndim == 3 and frame.shape[2] in (3, 4):
        bgr = frame[:, :, :3].astype(np.float32)
        alpha = frame[:, :, 3].copy() if frame.shape[2] == 4 else None
    else:
        raise ValueError("Expected a grayscale, BGR, or BGRA frame.")

    # 1-2. Original white-balance calibration or explicit B/G/R multipliers.
    if settings.coords is not None:
        gains = measure_balance_offset(frame, settings.coords, settings.hex)
    else:
        gains = settings.offset
    if gains is not None:
        bgr *= np.asarray(gains, dtype=np.float32)[None, None, :]
    bgr = np.clip(bgr, 0, 255).astype(np.uint8)

    # 3. Targeted blue remapping, preserving the source brightness gradient.
    if settings.remap_blue:
        hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
        hue, _, value = cv2.split(hsv)
        low, high = settings.blue_range
        blue_mask = cv2.inRange(hue, low, high).astype(np.float32) / 255.0
        blue_mask = cv2.GaussianBlur(blue_mask, (3, 3), 0)
        ratios = hex_to_bgr(settings.remap_blue)
        total = np.sum(ratios)
        if total > 0:
            ratios /= total
        v_float = value.astype(np.float32)
        target = cv2.merge(
            [v_float * ratios[0] * 3.0,
             v_float * ratios[1] * 3.0,
             v_float * ratios[2] * 3.0]
        )
        mask = blue_mask[:, :, None]
        bgr = np.clip(
            bgr.astype(np.float32) * (1.0 - mask) + target * mask,
            0,
            255,
        ).astype(np.uint8)

    bgr = bgr.astype(np.float32)

    # 4. Brightness.
    if settings.brightness != 0.0:
        bgr += settings.brightness

    # 5. Contrast around middle gray.
    if settings.contrast != 1.0:
        bgr = 127.5 + settings.contrast * (bgr - 127.5)

    # 6-8. Exposure, saturation, vibrance.
    if (
        settings.saturation != 1.0
        or settings.vibrance != 0.0
        or settings.exposure != 0.0
    ):
        hsv = cv2.cvtColor(
            np.clip(bgr, 0, 255).astype(np.uint8), cv2.COLOR_BGR2HSV
        ).astype(np.float32)
        hue, sat, val = cv2.split(hsv)
        if settings.exposure != 0.0:
            val *= 2.0 ** settings.exposure
        if settings.saturation != 1.0:
            sat *= settings.saturation
        if settings.vibrance != 0.0:
            vibrance_mask = (255.0 - sat) / 255.0
            sat += settings.vibrance * vibrance_mask * 50.0
        hsv = np.clip(cv2.merge([hue, sat, val]), 0, 255).astype(np.uint8)
        bgr = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR).astype(np.float32)

    # 9-10. Luminance-weighted highlight and shadow adjustments.
    if settings.highlights != 0.0 or settings.shadows != 0.0:
        gray = cv2.cvtColor(
            np.clip(bgr, 0, 255).astype(np.uint8), cv2.COLOR_BGR2GRAY
        ).astype(np.float32) / 255.0
        if settings.highlights != 0.0:
            hi_mask = np.clip((gray - 0.5) * 2.0, 0, 1)[:, :, None] ** 2
            bgr += settings.highlights * hi_mask * 25.0
        if settings.shadows != 0.0:
            sh_mask = np.clip((0.5 - gray) * 2.0, 0, 1)[:, :, None] ** 2
            bgr += settings.shadows * sh_mask * 25.0

    # 11. Gamma.
    bgr = np.clip(bgr, 0, 255).astype(np.uint8)
    if settings.gamma != 1.0:
        bgr = adjust_gamma(bgr, settings.gamma)

    # 12. Sharpen using an unsharp mask.
    if settings.sharpness > 0.0:
        blurred = cv2.GaussianBlur(bgr, (5, 5), 1.0)
        bgr = cv2.addWeighted(
            bgr, 1.0 + settings.sharpness, blurred, -settings.sharpness, 0
        )

    if alpha is not None:
        return np.dstack((bgr, alpha))
    return bgr