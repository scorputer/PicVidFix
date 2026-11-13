"""Bounding-box metadata regression tests without opening a Tk window."""

import unittest

from region_inspector import rectangle_from_corners, region_metadata


class RegionInspectorMathTests(unittest.TestCase):
    """Full-resolution ROI coordinates must be stable and unambiguous."""

    def test_normal_and_reversed_corner_drag(self):
        first = rectangle_from_corners((10, 20), (50, 80), 5000, 3500)
        reverse = rectangle_from_corners((50, 80), (10, 20), 5000, 3500)
        self.assertEqual(first, (10, 20, 41, 61))
        self.assertEqual(first, reverse)

    def test_drag_outside_image_clamps_to_bounds(self):
        box = rectangle_from_corners(
            (-100, -100), (9000, 9000), 5000, 3500
        )
        self.assertEqual(box, (0, 0, 5000, 3500))

    def test_one_pixel_box(self):
        self.assertEqual(
            rectangle_from_corners((5, 6), (5, 6), 50, 50),
            (5, 6, 1, 1),
        )

    def test_metadata_is_suitable_for_numpy_slicing(self):
        data = region_metadata(
            (500, 350, 1000, 700), 5000, 3500, "test.png"
        )
        self.assertEqual(data["schema"], "picvidfix-region-v1")
        self.assertEqual(data["region"]["x2"], 1500)
        self.assertEqual(data["region"]["y2"], 1050)
        self.assertEqual(data["normalized"]["x"], 0.1)
        self.assertEqual(data["normalized"]["height"], 0.2)

    def test_video_metadata_retains_frame_number(self):
        data = region_metadata((1, 2, 3, 4), 100, 100, "video.mp4", 17)
        self.assertEqual(data["image"]["frame_index"], 17)

    def test_invalid_region_rejected(self):
        with self.assertRaises(ValueError):
            region_metadata((4990, 3490, 20, 20), 5000, 3500, "x")

        with self.assertRaises(ValueError):
            rectangle_from_corners((1, 1), (2, 2), 0, 100)


if __name__ == "__main__":
    unittest.main()
