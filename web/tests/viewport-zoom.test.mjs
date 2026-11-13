import test from "node:test";
import assert from "node:assert/strict";

import {
  MIN_ZOOM,
  MAX_ZOOM,
  fitZoom,
  clampZoom,
  zoomDimensions,
  anchoredScrollDelta
} from "../viewport-zoom.js";

test("fit a 5000 by 3500 image within an 800 by 560 display", () => {
  assert.equal(fitZoom(5000, 3500, 800, 560), 0.16);
});

test("fit can scale down but never upscales small originals", () => {
  assert.equal(fitZoom(400, 300, 1000, 700), 1);
  assert.equal(fitZoom(5000, 3500, 0, 700), null);
});

test("zoom limits and native-scale output dimensions", () => {
  assert.equal(clampZoom(0.01), MIN_ZOOM);
  assert.equal(clampZoom(100), MAX_ZOOM);
  assert.equal(clampZoom(Infinity), null);
  assert.deepEqual(zoomDimensions(5000, 3500, 1), {
    width: 5000,
    height: 3500
  });
});

test("cursor anchored zoom retains the same image pixel position", () => {
  const oldRect = { left: 10, top: 20, width: 500, height: 350 };
  const newRect = { left: 10, top: 20, width: 1000, height: 700 };
  const result = anchoredScrollDelta(oldRect, newRect, 260, 195);
  assert.deepEqual(result, { x: 250, y: 175 });
});

test("invalid scroll geometry is rejected", () => {
  assert.equal(anchoredScrollDelta(
    { left: 0, top: 0, width: 0, height: 20 },
    { left: 0, top: 0, width: 300, height: 300 },
    3, 5
  ), null);
});
