"""Object segmentation, mask refinement and local-edit regression tests.

These tests use generated images so CI does not need a sample-photo library.
"""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path

import cv2
import numpy as np

from color_pipeline import ColorSettings, process_with_object
from object_dialog import original_pixel, prepare_selection_image
from object_mask import (
    ObjectCorrectionSettings,
    apply_object_correction,
    paint_object_mask,
    segment_object,
    mask_applies_to_frame,
)
from video_color_gui import prepare_preview_frame, save_corrected_image


class ObjectMaskTests(unittest.TestCase):
    """Mask creation and corrections must preserve unaffected source pixels."""

    def setUp(self) -> None:
        self.frame = np.full((180, 240, 3), (32, 72, 103), np.uint8)
        cv2.circle(self.frame, (110, 90), 38, (175, 145, 60), -1)
        self.binary = np.zeros((180, 240), dtype=np.uint8)
        cv2.circle(self.binary, (110, 90), 38, 255, -1)

    def test_grabcut_finds_foreground_in_rough_rectangle(self):
        found = segment_object(self.frame, (64, 44, 95, 95))
        self.assertEqual(found.shape, self.frame.shape[:2])
        self.assertEqual(found.dtype, np.uint8)
        self.assertGreater(found[90, 110], 0)
        self.assertEqual(int(found[5, 5]), 0)

    def test_grabcut_rejects_tiny_or_border_rectangle(self):
        with self.assertRaises(ValueError):
            segment_object(self.frame, (1, 1, 2, 2))
        with self.assertRaises(ValueError):
            segment_object(self.frame, (0, 0, 120, 90))

    def test_keep_and_remove_brush_are_non_destructive(self):
        original = self.binary.copy()
        removed = paint_object_mask(self.binary, 110, 90, 7, False)
        self.assertEqual(int(removed[90, 110]), 0)
        restored = paint_object_mask(removed, 110, 90, 4, True)
        self.assertEqual(int(restored[90, 110]), 255)
        np.testing.assert_array_equal(self.binary, original)

    def test_local_exposure_changes_object_not_background(self):
        source = self.frame.copy()
        result = apply_object_correction(
            self.frame,
            self.binary,
            ObjectCorrectionSettings(
                enabled=True, exposure_ev=1.0, feather_px=0,
            ),
        )
        self.assertGreater(int(result[90, 110, 0]), int(source[90, 110, 0]))
        np.testing.assert_array_equal(result[0, 0], source[0, 0])
        np.testing.assert_array_equal(self.frame, source)

    def test_local_saturation_changes_object_only(self):
        result = apply_object_correction(
            self.frame,
            self.binary,
            ObjectCorrectionSettings(
                enabled=True, saturation=0, feather_px=0,
            ),
        )
        self.assertLessEqual(
            int(np.ptp(result[90, 110, :3])), 2
        )
        np.testing.assert_array_equal(result[0, 0], self.frame[0, 0])

    def test_unselected_and_neutral_adjustments_are_exact_copies(self):
        disabled = ObjectCorrectionSettings(
            enabled=False, exposure_ev=2, saturation=0,
        )
        neutral = ObjectCorrectionSettings(enabled=True)
        for settings in (disabled, neutral):
            out = apply_object_correction(self.frame, self.binary, settings)
            np.testing.assert_array_equal(out, self.frame)
            self.assertIsNot(out, self.frame)

    def test_mask_strength_and_feather_are_bounded(self):
        half = apply_object_correction(
            self.frame, self.binary,
            ObjectCorrectionSettings(
                enabled=True, exposure_ev=1, strength=0.5, feather_px=0,
            ),
        )
        full = apply_object_correction(
            self.frame, self.binary,
            ObjectCorrectionSettings(
                enabled=True, exposure_ev=1, strength=1, feather_px=0,
            ),
        )
        original = int(self.frame[90, 110, 0])
        self.assertLess(
            int(half[90, 110, 0]) - original,
            int(full[90, 110, 0]) - original,
        )
        feathered = apply_object_correction(
            self.frame, self.binary,
            ObjectCorrectionSettings(
                enabled=True, exposure_ev=1, feather_px=4,
            ),
        )
        self.assertGreater(
            int(feathered[90, 150, 0]), int(self.frame[90, 150, 0])
        )

    def test_alpha_and_mask_resolution_preserved(self):
        bgra = np.dstack((
            self.frame,
            np.full((180, 240), 87, dtype=np.uint8),
        ))
        small_mask = cv2.resize(self.binary, (60, 45))
        result = apply_object_correction(
            bgra, small_mask,
            ObjectCorrectionSettings(enabled=True, exposure_ev=1),
        )
        np.testing.assert_array_equal(result[:, :, 3], bgra[:, :, 3])
        self.assertEqual(result.shape, bgra.shape)

    def test_selection_image_is_downscaled_for_large_photo(self):
        frame = np.full((3500, 5000, 3), 120, dtype=np.uint8)
        selection = prepare_selection_image(frame)
        self.assertEqual(selection.shape, (840, 1200, 3))
        self.assertEqual(frame.shape, (3500, 5000, 3))
        self.assertEqual(
            original_pixel((600, 420), (1200, 840), (5000, 3500)),
            (2500, 1750),
        )

    def test_preview_equals_saved_image_after_object_correction(self):
        correction = ObjectCorrectionSettings(
            enabled=True, exposure_ev=0.6, saturation=0.9, feather_px=2,
        )
        global_settings = ColorSettings(exposure=0.1)
        _, preview = prepare_preview_frame(
            self.frame, global_settings,
            max_width=500, max_height=500,
            object_mask=self.binary, object_settings=correction,
        )
        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / "before.png"
            destination = Path(root) / "after.png"
            self.assertTrue(cv2.imwrite(str(source), self.frame))
            save_corrected_image(
                source, destination, global_settings,
                object_mask=self.binary, object_settings=correction,
            )
            saved = cv2.imread(str(destination))
            np.testing.assert_array_equal(preview, saved)
        np.testing.assert_array_equal(
            process_with_object(
                self.frame, global_settings, self.binary, correction,
            ),
            preview,
        )

    def test_invalid_controls_and_masks_rejected(self):
        with self.assertRaises(ValueError):
            apply_object_correction(
                self.frame, self.binary,
                ObjectCorrectionSettings(enabled=True, exposure_ev=9),
            )
        with self.assertRaises(ValueError):
            apply_object_correction(
                self.frame, np.zeros((0, 0), dtype=np.uint8),
                ObjectCorrectionSettings(enabled=True),
            )

    def test_video_object_mask_applies_only_to_selected_frame_by_default(self):
        self.assertTrue(mask_applies_to_frame(
            is_video=True, current_frame=120, selected_frame=120,
            fixed_for_video=False,
        ))
        self.assertFalse(mask_applies_to_frame(
            is_video=True, current_frame=121, selected_frame=120,
            fixed_for_video=False,
        ))
        self.assertTrue(mask_applies_to_frame(
            is_video=True, current_frame=121, selected_frame=120,
            fixed_for_video=True,
        ))
        self.assertTrue(mask_applies_to_frame(
            is_video=False, current_frame=0, selected_frame=None,
            fixed_for_video=False,
        ))

    def test_preset_parameters_serializable_without_source_mask(self):
        settings = ObjectCorrectionSettings(
            enabled=True, exposure_ev=-0.4, saturation=1.1,
        )
        restored = ObjectCorrectionSettings(**asdict(settings))
        self.assertEqual(settings, restored)
        self.assertNotIn("mask", asdict(settings))


if __name__ == "__main__":
    unittest.main()
