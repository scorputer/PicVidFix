import test from "node:test";
import assert from "node:assert/strict";

import {
  rectangleFromCorners,
  formatRectangle,
  exportRectangle
} from "../region-box.js";

test("dragging top-left to bottom-right produces a half-open rectangle", () => {
  assert.deepEqual(
    rectangleFromCorners({ x: 10, y: 20 }, { x: 50, y: 80 }, 5000, 3500),
    { x: 10, y: 20, width: 41, height: 61 }
  );
});

test("opposite dragging direction gives exactly the same bounding box", () => {
  const a = rectangleFromCorners({ x: 2000, y: 1600 }, { x: 120, y: 30 }, 5000, 3500);
  const b = rectangleFromCorners({ x: 120, y: 30 }, { x: 2000, y: 1600 }, 5000, 3500);
  assert.deepEqual(a, b);
});

test("a click without a drag selects exactly one pixel", () => {
  assert.deepEqual(
    rectangleFromCorners({ x: 3, y: 4 }, { x: 3, y: 4 }, 15, 15),
    { x: 3, y: 4, width: 1, height: 1 }
  );
});

test("coordinates remain inside a 5000 by 3500 image", () => {
  const box = rectangleFromCorners(
    { x: -100, y: -100 }, { x: 99999, y: 99999 }, 5000, 3500
  );
  assert.deepEqual(box, { x: 0, y: 0, width: 5000, height: 3500 });
});

test("invalid corner positions and dimensions are rejected", () => {
  assert.equal(rectangleFromCorners(null, { x: 1, y: 1 }, 50, 50), null);
  assert.equal(rectangleFromCorners({ x: 1.5, y: 1 }, { x: 2, y: 3 }, 50, 50), null);
  assert.equal(rectangleFromCorners({ x: 1, y: 1 }, { x: 2, y: 3 }, 0, 50), null);
});

test("export metadata retains original pixel dimensions and normalized box", () => {
  const box = { x: 500, y: 350, width: 1000, height: 700 };
  const data = exportRectangle(box, 5000, 3500, "photo.png");
  assert.equal(data.schema, "picvidfix-region-v1");
  assert.deepEqual(data.image, {
    filename: "photo.png", width: 5000, height: 3500
  });
  assert.deepEqual(data.region, {
    x: 500, y: 350, width: 1000, height: 700, x2: 1500, y2: 1050
  });
  assert.deepEqual(data.normalized, {
    x: 0.1, y: 0.1, width: 0.2, height: 0.2
  });
});

test("invalid or out-of-image bounding boxes cannot be exported", () => {
  assert.throws(() => exportRectangle(null, 50, 50, "x"), /No valid/);
  assert.throws(() => exportRectangle({
    x: 40, y: 0, width: 20, height: 20
  }, 50, 50, "x"), /No valid/);
});

test("box readout is stable, including unselected state", () => {
  assert.equal(formatRectangle(null), "No region selected");
  assert.equal(formatRectangle({
    x: 20, y: 31, width: 80, height: 100
  }), "X: 20  Y: 31  W: 80  H: 100");
});
