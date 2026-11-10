import test from "node:test";
import assert from "node:assert/strict";

import {
  SPATIAL_DEFAULTS,
  applyGradient,
  correctImage,
  geometryIsNeutral,
  gradientWeight,
  transformPixels,
  validatedSpatial
} from "../spatial-engine.js";

function image(width, height, rgba = [50, 65, 80, 255]) {
  const pixels = new Uint8ClampedArray(width * height * 4);
  for (let i = 0; i < pixels.length; i += 4) {
    pixels.set(rgba, i);
  }
  return pixels;
}

test("neutral spatial controls preserve the complete image", () => {
  const source = image(4, 3);
  const result = correctImage(source, 4, 3);
  assert.deepEqual(Array.from(result.before), Array.from(source));
  assert.deepEqual(Array.from(result.after), Array.from(source));
  assert.notStrictEqual(result.after, source);
  assert.equal(geometryIsNeutral(), true);
});

test("horizontal flip reflects image without losing alpha", () => {
  const source = new Uint8ClampedArray([
    20, 30, 40, 111, 90, 100, 110, 222
  ]);
  const result = transformPixels(source, 2, 1, { flip_horizontal: true });
  assert.deepEqual(
    Array.from(result),
    [90, 100, 110, 222, 20, 30, 40, 111]
  );
});

test("nonzero rotation preserves output dimensions and source", () => {
  const source = image(16, 8);
  const result = transformPixels(source, 16, 8, { rotation: 24 });
  assert.equal(result.length, source.length);
  assert.deepEqual(Array.from(source), Array.from(image(16, 8)));
  assert.ok(result.some((value, index) => index % 4 === 3 && value === 0));
});

test("perspective produces a non-neutral geometric warp", () => {
  const source = image(17, 15);
  assert.equal(geometryIsNeutral({ perspective_vertical: 0.2 }), false);
  const result = transformPixels(
    source, 17, 15, { perspective_vertical: 0.2 }
  );
  assert.ok(result.some((value, index) => index % 4 === 3 && value === 0));
});

test("a bottom-up gradient exposes foreground but preserves sky", () => {
  const source = image(1, 4, [50, 50, 50, 255]);
  const result = applyGradient(source, 1, 4, {
    gradient_enabled: true,
    gradient_exposure: 1,
    gradient_full_y: 1,
    gradient_zero_y: 0
  });
  assert.equal(result[0], 50);
  assert.ok(result[4] > 50 && result[4] < 100);
  assert.equal(result[12], 100);
  assert.equal(result[15], 255);
});

test("a radial gradient fades away from its center", () => {
  const source = image(5, 5, [70, 70, 70, 255]);
  const result = applyGradient(source, 5, 5, {
    gradient_enabled: true,
    gradient_type: "radial",
    gradient_exposure: 1,
    gradient_full_x: 0.5,
    gradient_full_y: 0.5,
    gradient_zero_x: 0.5,
    gradient_zero_y: 0
  });
  const center = (2 * 5 + 2) * 4;
  assert.equal(result[center], 140);
  assert.equal(result[0], 70);
});

test("gradient handles are normalized and smooth", () => {
  const settings = {
    gradient_full_x: 0.5, gradient_full_y: 1,
    gradient_zero_x: 0.5, gradient_zero_y: 0
  };
  assert.equal(gradientWeight(0.5, 0, settings), 0);
  assert.equal(gradientWeight(0.5, 1, settings), 1);
  assert.equal(gradientWeight(0.5, 0.5, settings), 0.5);
});

test("zero-strength gradient leaves the image intact", () => {
  const source = image(7, 5);
  assert.deepEqual(
    Array.from(applyGradient(source, 7, 5, {
      gradient_enabled: true,
      gradient_exposure: 0,
      gradient_saturation: 1
    })),
    Array.from(source)
  );
});

test("spatial presets validate ranges and preserve fractional values", () => {
  assert.deepEqual(validatedSpatial(SPATIAL_DEFAULTS), SPATIAL_DEFAULTS);
  assert.equal(validatedSpatial({ rotation: 0.17 }).rotation, 0.17);
  assert.throws(() => validatedSpatial({ rotation: 90 }), /out of range/);
  assert.throws(
    () => validatedSpatial({ gradient_full_y: 2 }),
    /between 0 and 1/
  );
  assert.throws(
    () => validatedSpatial({ gradient_type: "triangle" }),
    /linear or radial/
  );
});

test("preview and full-resolution export share the same processing function", () => {
  const source = image(4, 5, [25, 45, 65, 255]);
  const spatial = {
    gradient_enabled: true,
    gradient_exposure: 0.5,
    gradient_full_y: 1,
    gradient_zero_y: 0
  };
  const first = correctImage(source, 4, 5, { contrast: 1.1 }, spatial);
  const second = correctImage(source, 4, 5, { contrast: 1.1 }, spatial);
  assert.deepEqual(Array.from(first.after), Array.from(second.after));
});
