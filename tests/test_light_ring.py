"""Regression tests for reversible virtual ring lighting correction."""

from __future__ import annotations

import unittest

import numpy as np

from color_pipeline import ColorSettings, process_frame
from light_ring import apply_light_ring, estimate_ring_ev, radial_mask


class RingLightTests(unittest.TestCase):
    """Metering, spatial falloff, highlight safety and export parity."""

    def test_zero_ev_preserves_exact_pixels(self):
        original = np.random.default_rng(23).integers(
            0, 256, (105, 130, 4), dtype=np.uint8
        )
        result = apply_light_ring(original, ev=0.0)
        np.testing.assert_array_equal(result, original)
        self.assertIsNot(result, original)

    def test_center_brightens_more_than_corners(self):
        image = np.full((101, 101, 3), 64, dtype=np.uint8)
        adjusted = apply_light_ring(
            image, ev=1.0, radius=0.6, feather=0.7
        )
        self.assertGreater(int(adjusted[50, 50, 0]), 105)
        self.assertLess(int(adjusted[0, 0, 0]), 80)
        np.testing.assert_array_equal(image, np.full_like(image, 64))

    def test_negative_ev_tames_a_center_hotspot(self):
        image = np.full((101, 101, 3), 176, dtype=np.uint8)
        adjusted = apply_light_ring(image, ev=-1.0)
        self.assertLess(int(adjusted[50, 50, 0]), 100)
        self.assertEqual(int(adjusted[0, 0, 0]), 176)

    def test_alpha_is_unchanged(self):
        source = np.full((101, 105, 4), 120, dtype=np.uint8)
        source[:, :, 3] = np.arange(105, dtype=np.uint8)[None, :]
        result = apply_light_ring(source, ev=0.7)
        np.testing.assert_array_equal(result[:, :, 3], source[:, :, 3])

    def test_highlight_protection_is_less_aggressive(self):
        original = np.full((90, 110, 3), 205, dtype=np.uint8)
        protected = apply_light_ring(
            original, ev=0.3, protect_highlights=True
        )
        unprotected = apply_light_ring(
            original, ev=0.3, protect_highlights=False
        )
        self.assertLess(int(protected[45, 55, 0]),
                        int(unprotected[45, 55, 0]))

    def test_estimator_suggests_ev_for_dark_center(self):
        image = np.full((320, 320, 3), 165, dtype=np.uint8)
        image[128:193, 128:193] = 65
        estimate, center, surrounding = estimate_ring_ev(
            image, radius=0.65
        )
        self.assertGreater(estimate, 0.8)
        self.assertLess(center, surrounding)

    def test_estimator_suggests_negative_ev_for_hotspot(self):
        image = np.full((320, 320, 3), 85, dtype=np.uint8)
        image[128:193, 128:193] = 190
        ev, center, outer = estimate_ring_ev(image, radius=0.65)
        self.assertLess(ev, -0.6)
        self.assertGreater(center, outer)

    def test_ring_center_can_move_to_foreground(self):
        image = np.full((101, 101, 3), 75, dtype=np.uint8)
        moved = apply_light_ring(
            image, ev=0.7, center_x=0.15, center_y=0.75,
            radius=0.18, feather=0.6,
        )
        self.assertGreater(int(moved[75, 15, 0]),
                           int(moved[50, 50, 0]))

    def test_estimator_neutral_for_uniform_scene(self):
        image = np.full((250, 300, 3), 120, dtype=np.uint8)
        value, center, outer = estimate_ring_ev(image)
        self.assertEqual(value, 0)
        self.assertEqual(center, outer)

    def test_shared_pipeline_includes_ring_effect(self):
        image = np.full((105, 105, 3), 75, dtype=np.uint8)
        neutral = process_frame(image, ColorSettings())
        ring = process_frame(image, ColorSettings(
            ring_enabled=True, ring_ev=1.0
        ))
        np.testing.assert_array_equal(neutral, image)
        self.assertGreater(int(ring[52, 52, 0]), 100)
        self.assertEqual(int(ring[0, 0, 0]), 75)

    def test_validation_rejects_bad_controls(self):
        with self.assertRaises(ValueError):
            radial_mask(100, 100, radius=-1)
        with self.assertRaises(ValueError):
            apply_light_ring(np.ones((10, 10, 3), dtype=np.float32), ev=1)
        with self.assertRaises(ValueError):
            apply_light_ring(np.ones((10, 10, 3), dtype=np.uint8), ev=4)


if __name__ == "__main__":
    unittest.main()
