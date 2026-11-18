"""Headless regression tests for independent video frame navigation."""

from __future__ import annotations

import unittest

import numpy as np

from reference_preview import (
    bounded_frame_index,
    frame_caption,
    preview_bgr,
)
from reference_dialog import ReferenceItem


class ReferencePreviewTests(unittest.TestCase):
    """Frame labels and numeric selection should never silently remap frames."""

    def test_valid_frame_index_accepts_zero_and_last(self):
        self.assertEqual(bounded_frame_index("0", 100), 0)
        self.assertEqual(bounded_frame_index("007", 100), 7)
        self.assertEqual(bounded_frame_index("99", 100), 99)
        self.assertEqual(bounded_frame_index(17, 100), 17)

    def test_frame_outside_video_rejected(self):
        for invalid in (-1, 100, "12.5", "12abc", "", None):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    bounded_frame_index(invalid, 100)

    def test_single_frame_video_index_is_zero(self):
        self.assertEqual(bounded_frame_index("0", 1), 0)
        with self.assertRaises(ValueError):
            bounded_frame_index("1", 1)

    def test_source_and_reference_video_captions_include_frame_numbers(self):
        label = frame_caption("scene.mp4", 28, 203)
        self.assertIn("Frame 28 / 202", label)
        self.assertIn("0-based", label)
        still_label = frame_caption("still.png", None, None)
        self.assertIn("Photo", still_label)

    def test_reference_item_persists_selected_video_frame_index(self):
        item = ReferenceItem(
            label="clip.mp4",
            frame=np.full((32, 32, 3), 75, dtype=np.uint8),
            video_path="clip.mp4", frame_index=17, frame_count=100,
        )
        self.assertIn("Frame 17 / 99", item.display_label())
        item.frame_index = 48
        self.assertIn("Frame 48 / 99", item.display_label())

    def test_preview_scaling_preserves_image_and_does_not_modify(self):
        original = np.full((600, 1000, 3), 128, dtype=np.uint8)
        result = preview_bgr(original, max_width=400, max_height=300)
        self.assertEqual(result.size, (400, 240))
        self.assertEqual(original.shape, (600, 1000, 3))
        self.assertEqual(int(original[0, 0, 0]), 128)

    def test_preview_rejects_non_byte_image(self):
        with self.assertRaises(ValueError):
            preview_bgr(np.ones((10, 10, 3), dtype=np.float32))


if __name__ == "__main__":
    unittest.main()
