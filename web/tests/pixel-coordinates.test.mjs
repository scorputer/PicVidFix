import test from "node:test";
import assert from "node:assert/strict";

import {
  pixelAtPointer,
  formatPixelPosition
} from "../pixel-coordinates.js";

const bounds = { left: 100, top: 50, width: 400, height: 200 };

test("maps a resized preview to original image pixels", () => {
  assert.deepEqual(
    pixelAtPointer(300, 150, bounds, 4000, 2000),
    { x: 2000, y: 1000 }
  );
});

test("upper-left image corner is pixel zero, zero", () => {
  assert.deepEqual(
    pixelAtPointer(100, 50, bounds, 4000, 2000),
    { x: 0, y: 0 }
  );
});

test("right and bottom edges clamp to the last pixel", () => {
  assert.deepEqual(
    pixelAtPointer(500, 250, bounds, 4000, 2000),
    { x: 3999, y: 1999 }
  );
});

test("coordinates outside image and invalid rectangles are rejected", () => {
  assert.equal(pixelAtPointer(99, 150, bounds, 4000, 2000), null);
  assert.equal(pixelAtPointer(300, 251, bounds, 4000, 2000), null);
  assert.equal(pixelAtPointer(200, 100, { ...bounds, width: 0 }, 4000, 2000), null);
  assert.equal(pixelAtPointer(200, 100, bounds, 0, 2000), null);
});

test("works for a single pixel image and fractional CSS sizing", () => {
  assert.deepEqual(
    pixelAtPointer(201, 91, {
      left: 100.5, top: 40.5, width: 401.25, height: 205.75
    }, 1, 1),
    { x: 0, y: 0 }
  );
});

test("readout formatting is friendly and stable", () => {
  assert.equal(formatPixelPosition(null), "X: —  Y: —");
  assert.equal(
    formatPixelPosition({ x: 1234, y: 987 }),
    "X: 1,234  Y: 987"
  );
});
