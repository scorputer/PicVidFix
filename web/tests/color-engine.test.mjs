import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import {
  CONTROLS,
  DEFAULTS,
  correctPixels,
  measureNeutralPatch,
  parseHex,
  validatedSettings
} from "../color-engine.js";

function pixel(red = 40, green = 80, blue = 120, alpha = 255) {
  return new Uint8ClampedArray([red, green, blue, alpha]);
}

test("neutral settings preserve RGBA pixels", () => {
  const source = pixel();
  const output = correctPixels(source, 1, 1, DEFAULTS);
  assert.deepEqual(Array.from(output), Array.from(source));
  assert.notStrictEqual(output, source);
});

test("processing does not mutate source", () => {
  const source = pixel();
  correctPixels(source, 1, 1, { ...DEFAULTS, contrast: 1.75 });
  assert.deepEqual(Array.from(source), [40, 80, 120, 255]);
});

test("brightness and channel gains match simple expected values", () => {
  assert.deepEqual(
    Array.from(correctPixels(pixel(), 1, 1, { brightness: 10 })),
    [50, 90, 130, 255]
  );
  assert.deepEqual(
    Array.from(correctPixels(pixel(), 1, 1, {
      red_gain: 2,
      green_gain: 1,
      blue_gain: 0.5
    })),
    [80, 80, 60, 255]
  );
});

test("positive exposure and gamma both brighten", () => {
  const source = pixel(40, 80, 120);
  const exposure = correctPixels(source, 1, 1, { exposure: 1 });
  const gamma = correctPixels(source, 1, 1, { gamma: 1.4 });
  assert.ok(exposure[2] > source[2]);
  assert.ok(gamma[2] > source[2]);
});

test("alpha survives all correction stages", () => {
  const source = pixel(25, 100, 230, 113);
  const result = correctPixels(source, 1, 1, {
    red_gain: 1.2,
    saturation: 1.5,
    gamma: 1.2,
    sharpness: 0.4
  });
  assert.equal(result[3], 113);
});

test("sharpening leaves a constant image unchanged to within one step", () => {
  const image = new Uint8ClampedArray(6 * 6 * 4);
  for (let i = 0; i < image.length; i += 4) {
    image.set([90, 120, 160, 255], i);
  }
  const result = correctPixels(image, 6, 6, { sharpness: 1.5 });
  for (let i = 0; i < image.length; i += 4) {
    for (let c = 0; c < 4; c++) {
      assert.ok(Math.abs(result[i + c] - image[i + c]) <= 1);
    }
  }
});

test("neutral patch sampling reports RGB gains", () => {
  const source = new Uint8ClampedArray([
    100, 125, 200, 255,
    100, 125, 200, 255
  ]);
  const result = measureNeutralPatch(
    source, 2, 1, [0, 0, 2, 1], "C8FA64"
  );
  assert.equal(result.red_gain, 2);
  assert.equal(result.green_gain, 2);
  assert.equal(result.blue_gain, 0.5);
});

test("invalid settings and dimensions are rejected", () => {
  assert.throws(() => validatedSettings({ gamma: 0 }), /Gamma/);
  assert.throws(() => validatedSettings({ hue_low: 140, hue_high: 120 }), /Blue hue/);
  assert.throws(() => correctPixels(pixel(), 2, 1), /Invalid RGBA/);
  assert.throws(() => parseHex("nothex"), /hexadecimal/);
});

test("fractional settings survive preset validation", () => {
  assert.equal(validatedSettings({ red_gain: 1.137 }).red_gain, 1.137);
  assert.equal(CONTROLS.length, 14);
});

test("selective blue remapping changes blue, not red, in a single-pixel sample", () => {
  const blue = correctPixels(pixel(0, 0, 200), 1, 1, {
    remap_enabled: true,
    remap_hex: "FF8800",
    hue_low: 95,
    hue_high: 135
  });
  const red = correctPixels(pixel(200, 0, 0), 1, 1, {
    remap_enabled: true,
    remap_hex: "FF8800",
    hue_low: 95,
    hue_high: 135
  });
  assert.ok(blue[0] > 0);
  assert.deepEqual(Array.from(red), [200, 0, 0, 255]);
});

test("web UI contains all required controls and local-only code", () => {
  const here = dirname(fileURLToPath(import.meta.url));
  const html = readFileSync(resolve(here, "../index.html"), "utf8");
  const app = readFileSync(resolve(here, "../app.js"), "utf8");
  for (const name of [
    "openButton", "originalCanvas", "correctedCanvas", "downloadButton",
    "compareSlider", "sampleButton", "loadPresetButton", "savePresetButton"
  ]) {
    assert.match(html, new RegExp('id="' + name + '"'));
  }
  assert.match(app, /createImageBitmap/);
  assert.match(app, /canvas.toBlob/);
  assert.doesNotMatch(app, /fetch\s*\(|XMLHttpRequest/);
});
