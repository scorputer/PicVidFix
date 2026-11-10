"""Small dependency-level regression tests for the shared color pipeline."""

import unittest

import numpy as np

from color_pipeline import ColorSettings, process_frame


class ColorPipelineSmokeTests(unittest.TestCase):
    """Verify neutral corrections and alpha preservation."""

    def setUp(self):
        self.image = np.random.default_rng(2026).integers(
            0, 256, (32, 48, 3), dtype=np.uint8
        )

    def test_neutral_settings_preserve_pixels(self):
        actual = process_frame(self.image, ColorSettings())
        np.testing.assert_array_equal(actual, self.image)

    def test_source_image_is_not_mutated(self):
        original = self.image.copy()
        process_frame(self.image, ColorSettings(contrast=1.2))
        np.testing.assert_array_equal(self.image, original)

    def test_alpha_channel_preserved(self):
        rgba = np.dstack(
            (self.image, np.full((32, 48), 117, dtype=np.uint8))
        )
        result = process_frame(rgba, ColorSettings(exposure=0.3))
        np.testing.assert_array_equal(result[:, :, 3], rgba[:, :, 3])


if __name__ == "__main__":
    unittest.main()
