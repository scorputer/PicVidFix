"""Non-destructive virtual ring-light exposure leveling.

The correction is a radial, feathered EV field. It can add virtual fill light
to a dark central region or attenuate a bright central hotspot. A suggested
EV from center-vs-edge metering is heuristic, not scene understanding.

No image samples are modified by the estimation functions.
"""

from __future__ import annotations

import math

import cv2
import numpy as np


def radial_mask(
    width: int,
    height: int,
    *,
    center_x: float = 0.5,
    center_y: float = 0.5,
    radius: float = 0.65,
    feather: float = 0.75,
) -> np.ndarray:
    """Build a radial mask using fractions of the shorter image dimension.

    An inner disk receives full correction. The rest feathers to zero at the
    selected radius. A large feather removes the plateau for a soft ring light.
    """
    if not (width > 0 and height > 0):
        raise ValueError("Image dimensions must be positive.")
    if not all(math.isfinite(value) for value in (
        center_x, center_y, radius, feather
    )):
        raise ValueError("Ring parameters must be finite.")
    if not (0 <= center_x <= 1 and 0 <= center_y <= 1
            and 0.05 <= radius <= 2 and 0 <= feather <= 1):
        raise ValueError("Ring position, radius or feather is out of range.")

    unit = float(min(width, height))
    yy, xx = np.ogrid[:height, :width]
    distance = np.hypot(
        (xx - center_x * (width - 1)) / unit,
        (yy - center_y * (height - 1)) / unit,
    ).astype(np.float32)
    inner = radius * (1.0 - feather)
    if feather <= 1e-6:
        return (distance <= radius).astype(np.float32)
    position = np.clip((distance - inner) / (radius - inner), 0.0, 1.0)
    return (1.0 - position * position * (3.0 - 2.0 * position)).astype(np.float32)


def apply_light_ring(
    image: np.ndarray,
    *,
    ev: float,
    center_x: float = 0.5,
    center_y: float = 0.5,
    radius: float = 0.65,
    feather: float = 0.75,
    protect_highlights: bool = True,
) -> np.ndarray:
    """Apply a signed radial exposure correction without changing input data.

    Gray, BGR, and BGRA are supported. Alpha is preserved exactly.
    Zero EV is a byte-exact copy. Output remains uint8.
    """
    if not isinstance(image, np.ndarray) or image.dtype != np.uint8:
        raise ValueError("Ring correction expects a uint8 image.")
    if image.ndim != 2 and (
        image.ndim != 3 or image.shape[2] not in (3, 4)
    ):
        raise ValueError("Ring correction expects gray, BGR or BGRA input.")
    if not math.isfinite(ev) or not -3 <= ev <= 3:
        raise ValueError("Ring exposure must be between -3 and +3 EV.")
    if ev == 0:
        return image.copy()

    height, width = image.shape[:2]
    weights = radial_mask(
        width, height, center_x=center_x, center_y=center_y,
        radius=radius, feather=feather,
    )

    channels = image if image.ndim == 2 else image[:, :, :3]
    original = channels.astype(np.float32)
    if ev > 0 and protect_highlights:
        # Avoid pushing already bright pixels to white. Does not recover
        # detail that was clipped in the original.
        if channels.ndim == 3:
            luminance = cv2.cvtColor(
                channels, cv2.COLOR_BGR2GRAY
            ).astype(np.float32)
        else:
            luminance = original
        protection = 1.0 - 0.9 * np.clip(
            (luminance - 175.0) / 80.0, 0.0, 1.0
        )
        weights *= protection.astype(np.float32)

    multiplier = np.exp2(weights * ev)
    if original.ndim == 3:
        multiplier = multiplier[:, :, None]
    corrected = np.clip(
        original * multiplier, 0.0, 255.0
    ).astype(np.uint8)
    if image.ndim == 3 and image.shape[2] == 4:
        return np.dstack((corrected, image[:, :, 3].copy()))
    return corrected


def estimate_ring_ev(
    image: np.ndarray,
    *,
    center_x: float = 0.5,
    center_y: float = 0.5,
    radius: float = 0.65,
    limit: float = 1.5,
) -> tuple[float, float, float]:
    """Estimate EV to bring the center toward a surrounding annulus.

    Returns (suggested EV, center median, surrounding median), using sRGB gray
    as an approximate meter. Strong scene content differences can fool the
    meter, so the caller should require explicit user approval.
    """
    if not 0 < limit <= 3:
        raise ValueError("Meter limit must be between zero and three.")
    if image.dtype != np.uint8:
        raise ValueError("Ring metering requires uint8 data.")
    if image.ndim == 2:
        gray = image
    elif image.ndim == 3 and image.shape[2] in (3, 4):
        gray = cv2.cvtColor(image[:, :, :3], cv2.COLOR_BGR2GRAY)
    else:
        raise ValueError("Ring metering requires gray, BGR or BGRA data.")
    height, width = gray.shape
    if min(height, width) < 16:
        raise ValueError("Image is too small for radial light measurement.")
    scale = min(1, 700 / max(width, height))
    if scale < 1:
        gray = cv2.resize(
            gray, (max(16, round(width * scale)),
                   max(16, round(height * scale))), interpolation=cv2.INTER_AREA
        )
    height, width = gray.shape
    yy, xx = np.ogrid[:height, :width]
    unit = min(width, height)
    distance = np.hypot(
        (xx - center_x * (width - 1)) / unit,
        (yy - center_y * (height - 1)) / unit,
    )
    valid = (gray >= 12) & (gray <= 242)
    center = gray[valid & (distance <= radius * 0.22)]
    surrounding = gray[valid & (distance >= radius * 0.72)
                       & (distance <= radius * 1.05)]
    if center.size < 32 or surrounding.size < 64:
        raise ValueError(
            "Not enough unclipped center and surrounding pixels to meter."
        )
    center_median = float(np.median(center))
    surround_median = float(np.median(surrounding))
    ev = float(np.clip(
        math.log2(max(1.0, surround_median) / max(1.0, center_median)),
        -limit, limit,
    ))
    return ev, center_median, surround_median
