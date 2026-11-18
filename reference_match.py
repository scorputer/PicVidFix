"""Reference-guided image exposure and color analysis for PicVidFix.

This is an advisory engine: never alter source frames or silently apply edits.
It aligns overlapping *same-scene* photos using ORB and RANSAC, then
compares corresponding unclipped pixels on a low-resolution analysis grid.
Unrelated scenes are reported as unmatched, not treated as overlap.
No third-party dependencies beyond the existing OpenCV and NumPy packages.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import cv2
import numpy as np

GRID_ROWS = 6
GRID_COLUMNS = 8
MAX_ANALYSIS_SIDE = 1200


@dataclass(frozen=True)
class ReferenceResult:
    """Outcome of one source/reference comparison."""

    label: str
    matched: bool
    reason: str
    features: int = 0
    inliers: int = 0
    overlap_fraction: float = 0.0
    confidence: float = 0.0
    exposure_ev: float | None = None
    balance_bgr: tuple[float, float, float] | None = None
    tile_ev: tuple[tuple[float | None, ...], ...] = ()


@dataclass(frozen=True)
class ReferenceReport:
    """A weighted, spatial consensus from successfully aligned references."""

    references: tuple[ReferenceResult, ...]
    exposure_ev: float | None
    balance_bgr: tuple[float, float, float] | None
    tile_ev: tuple[tuple[float | None, ...], ...]
    tile_support: tuple[tuple[int, ...], ...]
    disagreement_ev: float | None
    warnings: tuple[str, ...]


def _as_bgr(frame: np.ndarray) -> np.ndarray:
    """Accept only uint8 source images, preserving the caller's pixel array."""
    if not isinstance(frame, np.ndarray) or frame.dtype != np.uint8:
        raise ValueError("Expected an 8-bit OpenCV image.")
    if frame.ndim == 2:
        return cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
    if frame.ndim == 3 and frame.shape[2] in (3, 4):
        return frame[:, :, :3]
    raise ValueError("Expected grayscale, BGR, or BGRA image data.")


def _scale_for_analysis(frame: np.ndarray) -> np.ndarray:
    """Limit RAM, matching cost, and analysis latency for large media."""
    height, width = frame.shape[:2]
    if min(width, height) < 4:
        raise ValueError("Reference image must be at least four pixels per side.")
    scale = min(1.0, MAX_ANALYSIS_SIDE / max(width, height))
    if scale == 1.0:
        return frame.copy()
    return cv2.resize(
        frame,
        (max(4, round(width * scale)), max(4, round(height * scale))),
        interpolation=cv2.INTER_AREA,
    )


def _linear_luminance(image: np.ndarray) -> np.ndarray:
    """Approximate scene luminance by linearizing sRGB before comparison."""
    bgr = image.astype(np.float32) / 255.0
    linear = np.where(
        bgr <= 0.04045,
        bgr / 12.92,
        ((bgr + 0.055) / 1.055) ** 2.4,
    )
    return (
        linear[:, :, 0] * 0.0722
        + linear[:, :, 1] * 0.7152
        + linear[:, :, 2] * 0.2126
    )


def _empty_result(label: str, reason: str, features: int = 0) -> ReferenceResult:
    return ReferenceResult(label=label, matched=False, reason=reason, features=features)


def _aligned_overlap(
    source: np.ndarray, reference: np.ndarray
) -> tuple[np.ndarray, np.ndarray, int, int] | tuple[None, None, int, int]:
    """Align reference coordinates into source coordinates using ORB and RANSAC."""
    detector = cv2.ORB_create(nfeatures=2200, fastThreshold=12)
    source_gray = cv2.cvtColor(source, cv2.COLOR_BGR2GRAY)
    ref_gray = cv2.cvtColor(reference, cv2.COLOR_BGR2GRAY)
    src_points, src_desc = detector.detectAndCompute(source_gray, None)
    ref_points, ref_desc = detector.detectAndCompute(ref_gray, None)

    if src_desc is None or ref_desc is None or len(src_points) < 12:
        return None, None, 0, 0

    knn = cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(ref_desc, src_desc, k=2)
    good = [
        pair[0] for pair in knn
        if len(pair) == 2 and pair[0].distance < 0.77 * pair[1].distance
    ]
    if len(good) < 12:
        return None, None, len(good), 0

    ref_coords = np.float32(
        [ref_points[match.queryIdx].pt for match in good]
    ).reshape(-1, 1, 2)
    source_coords = np.float32(
        [src_points[match.trainIdx].pt for match in good]
    ).reshape(-1, 1, 2)

    transform, inlier_mask = cv2.findHomography(
        ref_coords, source_coords, cv2.RANSAC, 3.5
    )
    inliers = int(np.count_nonzero(inlier_mask)) if inlier_mask is not None else 0
    if transform is None or inliers < 12 or inliers / len(good) < 0.45:
        return None, None, len(good), inliers

    height, width = source.shape[:2]
    ref_h, ref_w = reference.shape[:2]
    projected = cv2.perspectiveTransform(
        np.float32([
            [[0, 0]], [[ref_w - 1, 0]],
            [[ref_w - 1, ref_h - 1]], [[0, ref_h - 1]],
        ]),
        transform,
    ).reshape(4, 2)
    if not np.isfinite(projected).all():
        return None, None, len(good), inliers

    # Reject degenerate or implausibly distorted polygons. Dynamic content or
    # repeated textures can produce a high inlier count for a false geometry.
    area = abs(cv2.contourArea(projected))
    if area < 0.03 * width * height or area > 6 * width * height:
        return None, None, len(good), inliers
    if not cv2.isContourConvex(projected.astype(np.float32)):
        return None, None, len(good), inliers

    aligned = cv2.warpPerspective(
        reference, transform, (width, height),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
    )
    valid = cv2.warpPerspective(
        np.full((ref_h, ref_w), 255, dtype=np.uint8),
        transform, (width, height),
        flags=cv2.INTER_NEAREST,
        borderMode=cv2.BORDER_CONSTANT,
    ) > 0
    return aligned, valid, len(good), inliers


def compare_reference(
    source: np.ndarray,
    reference: np.ndarray,
    label: str = "Reference",
) -> ReferenceResult:
    """Suggest exposure and channel-balance shifts only where images overlap."""
    source_small = _scale_for_analysis(_as_bgr(source))
    reference_small = _scale_for_analysis(_as_bgr(reference))
    aligned, mask, features, inliers = _aligned_overlap(
        source_small, reference_small
    )
    if aligned is None or mask is None:
        return _empty_result(label, "Not enough reliable matching features", features)

    overlap_fraction = float(np.mean(mask))
    if overlap_fraction < 0.07:
        return _empty_result(label, "Detected overlap is too small", features)

    src_y = _linear_luminance(source_small)
    ref_y = _linear_luminance(aligned)
    good = mask & (src_y > 0.006) & (ref_y > 0.006)
    good &= (src_y < 0.88) & (ref_y < 0.88)
    if np.count_nonzero(good) < 500:
        return _empty_result(label, "Overlap lacks usable unclipped pixels", features)

    with np.errstate(divide="ignore", invalid="ignore"):
        ev_map = np.log2(np.maximum(ref_y, 1e-5) / np.maximum(src_y, 1e-5))
    exposure = float(np.clip(np.median(ev_map[good]), -3.0, 3.0))
    residual_mad = float(np.median(np.abs(ev_map[good] - exposure)))
    inlier_fraction = inliers / max(1, features)
    quality = float(np.clip(
        (inliers / 65.0) ** 0.5 * inlier_fraction
        * min(1.0, overlap_fraction / 0.30)
        * max(0.1, 1.0 - residual_mad / 1.5),
        0.0, 1.0,
    ))

    source_pixels = source_small[good].astype(np.float32)
    ref_pixels = aligned[good].astype(np.float32)
    ratios = (ref_pixels + 5.0) / (source_pixels + 5.0)
    medians = np.median(ratios, axis=0)
    # Normalize away exposure, leaving a tentative *chromatic* balance.
    medians = medians / np.exp(np.mean(np.log(np.maximum(medians, 1e-4))))
    bgr_balance = tuple(float(v) for v in np.clip(medians, 0.67, 1.5))

    height, width = source_small.shape[:2]
    rows: list[tuple[float | None, ...]] = []
    for row in range(GRID_ROWS):
        cells: list[float | None] = []
        y0, y1 = row * height // GRID_ROWS, (row + 1) * height // GRID_ROWS
        for col in range(GRID_COLUMNS):
            x0, x1 = col * width // GRID_COLUMNS, (col + 1) * width // GRID_COLUMNS
            tile_good = good[y0:y1, x0:x1]
            # Require robust coverage; a few mismatched features aren't enough.
            if np.count_nonzero(tile_good) < 70:
                cells.append(None)
            else:
                value = float(np.median(ev_map[y0:y1, x0:x1][tile_good]))
                cells.append(float(np.clip(value, -3.0, 3.0)))
        rows.append(tuple(cells))

    return ReferenceResult(
        label=label,
        matched=True,
        reason="Same-scene correspondence found",
        features=features,
        inliers=inliers,
        overlap_fraction=overlap_fraction,
        confidence=quality,
        exposure_ev=exposure,
        balance_bgr=bgr_balance,
        tile_ev=tuple(rows),
    )


def analyze_references(
    source: np.ndarray,
    references: Sequence[tuple[str, np.ndarray]],
) -> ReferenceReport:
    """Combine multiple overlap comparisons without inventing missing data."""
    if not references:
        raise ValueError("Add at least one reference photo or video frame.")
    results = tuple(
        compare_reference(source, image, label)
        for label, image in references
    )
    successful = [
        result for result in results
        if result.matched and result.exposure_ev is not None
    ]
    warning: list[str] = []
    if not successful:
        warning.append(
            "No reliable same-scene overlap. Do not apply automatic color shifts."
        )
        return ReferenceReport(
            results, None, None,
            tuple(tuple(None for _ in range(GRID_COLUMNS)) for _ in range(GRID_ROWS)),
            tuple(tuple(0 for _ in range(GRID_COLUMNS)) for _ in range(GRID_ROWS)),
            None, tuple(warning),
        )

    weights = np.asarray([
        max(0.05, result.confidence) for result in successful
    ], dtype=np.float64)
    ev_values = np.asarray([
        result.exposure_ev for result in successful
    ], dtype=np.float64)
    global_ev = float(np.average(ev_values, weights=weights))
    dispersion = float(np.sqrt(np.average(
        (ev_values - global_ev) ** 2, weights=weights
    )))
    if dispersion > 0.4:
        warning.append(
            "References disagree by over 0.4 EV; review conditions before applying."
        )
    if len(successful) != len(results):
        warning.append(
            f"{len(results) - len(successful)} reference(s) could not be aligned."
        )

    gains = np.asarray([
        result.balance_bgr for result in successful
    ], dtype=np.float64)
    combined_gains = tuple(float(v) for v in np.exp(
        np.average(np.log(gains), axis=0, weights=weights)
    ))

    tile_rows: list[tuple[float | None, ...]] = []
    support_rows: list[tuple[int, ...]] = []
    for row in range(GRID_ROWS):
        values: list[float | None] = []
        support: list[int] = []
        for col in range(GRID_COLUMNS):
            contributing = [
                (result.tile_ev[row][col], weight)
                for result, weight in zip(successful, weights)
                if result.tile_ev[row][col] is not None
            ]
            support.append(len(contributing))
            if not contributing:
                values.append(None)
            else:
                values.append(float(np.average(
                    [value for value, _ in contributing],
                    weights=[weight for _, weight in contributing],
                )))
        tile_rows.append(tuple(values))
        support_rows.append(tuple(support))

    return ReferenceReport(
        references=results,
        exposure_ev=global_ev,
        balance_bgr=combined_gains,
        tile_ev=tuple(tile_rows),
        tile_support=tuple(support_rows),
        disagreement_ev=dispersion,
        warnings=tuple(warning),
    )


def format_report(report: ReferenceReport) -> str:
    """Human-readable summary for a GUI status pane."""
    lines = []
    for item in report.references:
        if item.matched:
            lines.append(
                f"{item.label}: overlap {item.overlap_fraction:.0%} | "
                f"{item.inliers}/{item.features} matches | "
                f"confidence {item.confidence:.0%} | "
                f"suggested {item.exposure_ev:+.2f} EV"
            )
        else:
            lines.append(f"{item.label}: NO MATCH ({item.reason})")

    if report.exposure_ev is None:
        lines.append("No correction recommendation is available.")
    else:
        gains = report.balance_bgr or (1.0, 1.0, 1.0)
        lines.append(
            f"Consensus: {report.exposure_ev:+.2f} EV; "
            f"relative B/G/R gains: {gains[0]:.3f} / "
            f"{gains[1]:.3f} / {gains[2]:.3f}"
        )
        lines.append(
            "Grid below suggests local shifts; blank cells mean no reliable data."
        )
        for row in report.tile_ev:
            lines.append(
                " ".join("  ·  " if x is None else f"{x:+.1f}" for x in row)
            )
    lines.extend("Warning: " + item for item in report.warnings)
    return "\n".join(lines)
