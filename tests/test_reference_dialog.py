"""Pure/utility-level regression tests for the reference dialog.

Tk windows are intentionally not constructed in headless CI. Geometry,
matching, and image resizing are validated without requiring a display.
"""

from __future__ import annotations

import unittest

import numpy as np

from reference_dialog import describe_reference, prepare_reference
from reference_match import (
    MAX_ANALYSIS_SIDE,
    analyze_references,
)


class ReferenceDialogUtilityTests(unittest.TestCase):
    """Ensure 5K image references don't require full-resolution cache copies."""

    def test_large_reference_is_downsampled_preserving_aspect_ratio(self):
        frame = np.full((3500, 5000, 3), 127, dtype=np.uint8)
        small = prepare_reference(frame)
        self.assertEqual(small.shape, (840, MAX_ANALYSIS_SIDE, 3))
        self.assertEqual(small.dtype, np.uint8)
        self.assertEqual(frame.shape, (3500, 5000, 3))

    def test_small_reference_is_copied_not_mutated(self):
        frame = np.full((32, 48, 3), 80, dtype=np.uint8)
        clone = prepare_reference(frame)
        self.assertTrue(np.array_equal(frame, clone))
        self.assertIsNot(frame, clone)
        clone[0, 0, 0] = 2
        self.assertEqual(frame[0, 0, 0], 80)

    def test_no_overlap_diagnostic_explains_failure(self):
        source = np.zeros((120, 160, 3), dtype=np.uint8)
        other = np.full_like(source, 100)
        report = analyze_references(source, [("unrelated", other)])
        message = describe_reference(report)
        self.assertIn("0 of 1 reference(s) aligned", message)
        self.assertIn("not a calibrated probability", message)


if __name__ == "__main__":
    unittest.main()
