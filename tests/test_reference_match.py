"""Regression tests for overlap-guided multi-reference color analysis."""

from __future__ import annotations

import unittest

import cv2
import numpy as np

from reference_match import (
    GRID_COLUMNS,
    GRID_ROWS,
    analyze_references,
    compare_reference,
    format_report,
)


def patterned_scene(seed: int = 2026) -> np.ndarray:
    """Make a reproducible feature-rich scene, independent of test photos."""
    rng = np.random.default_rng(seed)
    frame = rng.integers(
        30, 165, size=(420, 620, 3), dtype=np.uint8
    )
    # Add big corners/edges to keep ORB robust when exposure shifts.
    for i in range(24):
        x = int(rng.integers(30, 550))
        y = int(rng.integers(30, 360))
        cv2.rectangle(
            frame, (x, y), (x + 15, y + 12),
            (int(i * 7 % 255), int(i * 3 % 255), int(i * 11 % 255)),
            thickness=-1,
        )
    return frame


class ReferenceMatchingTests(unittest.TestCase):
    """Same-scene references should align; unrelated ones must not be trusted."""

    def setUp(self) -> None:
        self.source = patterned_scene()

    def test_identical_reference_recommends_no_adjustment(self):
        source_copy = self.source.copy()
        result = compare_reference(self.source, self.source, "baseline")
        self.assertTrue(result.matched, result.reason)
        self.assertGreater(result.inliers, 12)
        self.assertGreater(result.overlap_fraction, 0.7)
        self.assertAlmostEqual(result.exposure_ev, 0.0, places=3)
        for gain in result.balance_bgr:
            self.assertAlmostEqual(gain, 1.0, places=3)
        np.testing.assert_array_equal(self.source, source_copy)

    def test_exposure_increase_detected_in_matched_scene(self):
        light = np.clip(
            self.source.astype(np.float32) * 1.17, 0, 255
        ).astype(np.uint8)
        result = compare_reference(self.source, light, "brighter")
        self.assertTrue(result.matched, result.reason)
        self.assertGreater(result.exposure_ev, 0.15)
        self.assertGreater(result.confidence, 0.05)

    def test_translated_partial_overlap_is_recognized(self):
        shifted = cv2.warpAffine(
            self.source,
            np.float32([[1, 0, 65], [0, 1, 38]]),
            (self.source.shape[1], self.source.shape[0]),
        )
        result = compare_reference(self.source, shifted, "translated")
        self.assertTrue(result.matched, result.reason)
        self.assertGreater(result.overlap_fraction, 0.4)

    def test_flat_frame_does_not_claim_overlap(self):
        blank = np.full_like(self.source, 128)
        result = compare_reference(self.source, blank, "no texture")
        self.assertFalse(result.matched)
        self.assertIsNone(result.exposure_ev)

    def test_multiple_references_contribute_to_spatial_grid(self):
        bright = np.clip(
            self.source.astype(np.float32) * 1.16, 0, 255
        ).astype(np.uint8)
        neutral = self.source.copy()
        report = analyze_references(
            self.source,
            [("bright", bright), ("neutral", neutral)],
        )
        self.assertEqual(len(report.references), 2)
        self.assertTrue(all(item.matched for item in report.references))
        self.assertGreater(report.exposure_ev, 0.05)
        self.assertLess(report.exposure_ev, 0.7)
        self.assertEqual(len(report.tile_ev), GRID_ROWS)
        self.assertEqual(len(report.tile_ev[0]), GRID_COLUMNS)
        self.assertGreater(max(map(max, report.tile_support)), 1)
        self.assertIn("Consensus", format_report(report))

    def test_no_match_returns_unknown_not_zero(self):
        report = analyze_references(
            self.source,
            [("flat", np.full_like(self.source, 128))],
        )
        self.assertIsNone(report.exposure_ev)
        self.assertEqual(report.tile_support[0][0], 0)
        self.assertIsNone(report.tile_ev[0][0])
        self.assertTrue(report.warnings)
        self.assertIn("NO MATCH", format_report(report))

    def test_input_validation(self):
        with self.assertRaises(ValueError):
            analyze_references(self.source, [])
        with self.assertRaises(ValueError):
            compare_reference(
                self.source.astype(np.float32), self.source
            )
        with self.assertRaises(ValueError):
            compare_reference(self.source, np.zeros((2, 2, 3), dtype=np.uint8))


if __name__ == "__main__":
    unittest.main()
