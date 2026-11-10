/**
 * PicVidFix Web spatial effects.
 *
 * Geometry is a reversible projective warp. Local adjustments use a feathered
 * linear or radial mask. The same code runs in preview and export workers.
 * This module has no DOM dependencies and does not upload data.
 */

import { correctPixels } from "./color-engine.js";

export const SPATIAL_DEFAULTS = Object.freeze({
  rotation: 0,
  perspective_vertical: 0,
  perspective_horizontal: 0,
  flip_horizontal: false,
  flip_vertical: false,
  gradient_enabled: false,
  gradient_type: "linear",
  gradient_exposure: 0.85,
  gradient_saturation: 1,
  gradient_full_x: 0.5,
  gradient_full_y: 0.93,
  gradient_zero_x: 0.5,
  gradient_zero_y: 0.25
});

export const SPATIAL_CONTROLS = Object.freeze([
  { id: "rotation", label: "Straighten (degrees)", min: -45, max: 45, step: 0.1 },
  { id: "perspective_vertical", label: "Vertical perspective", min: -0.45, max: 0.45, step: 0.01 },
  { id: "perspective_horizontal", label: "Horizontal perspective", min: -0.45, max: 0.45, step: 0.01 },
  { id: "gradient_exposure", label: "Regional exposure (EV)", min: -3, max: 3, step: 0.01 },
  { id: "gradient_saturation", label: "Regional saturation", min: 0, max: 3, step: 0.01 }
]);

function clamp(value, low, high) {
  return Math.min(high, Math.max(low, value));
}

export function validatedSpatial(input = {}) {
  if (!input || typeof input !== "object" || Array.isArray(input)) {
    throw new Error("Invalid geometry or regional-effects preset.");
  }
  const settings = { ...SPATIAL_DEFAULTS };
  for (const control of SPATIAL_CONTROLS) {
    if (input[control.id] === undefined) continue;
    const value = Number(input[control.id]);
    if (!Number.isFinite(value) || value < control.min || value > control.max) {
      throw new Error(control.label + " is out of range.");
    }
    settings[control.id] = value;
  }
  for (const id of [
    "gradient_full_x", "gradient_full_y", "gradient_zero_x", "gradient_zero_y"
  ]) {
    if (input[id] === undefined) continue;
    const value = Number(input[id]);
    if (!Number.isFinite(value) || value < 0 || value > 1) {
      throw new Error("Gradient handle positions must be between 0 and 1.");
    }
    settings[id] = value;
  }
  settings.flip_horizontal = Boolean(input.flip_horizontal ?? false);
  settings.flip_vertical = Boolean(input.flip_vertical ?? false);
  settings.gradient_enabled = Boolean(input.gradient_enabled ?? false);
  settings.gradient_type = input.gradient_type ?? "linear";
  if (!["linear", "radial"].includes(settings.gradient_type)) {
    throw new Error("Gradient mode must be linear or radial.");
  }
  return settings;
}

/**
 * Solve an 8 × 8 linear system with partial pivoting.
 * The homography is calculated only once per image, not per pixel.
 */
function solve8(matrix, vector) {
  const a = matrix.map((row, index) => [...row, vector[index]]);
  for (let col = 0; col < 8; col++) {
    let pivot = col;
    for (let row = col + 1; row < 8; row++) {
      if (Math.abs(a[row][col]) > Math.abs(a[pivot][col])) pivot = row;
    }
    if (Math.abs(a[pivot][col]) < 1e-9) {
      throw new Error("Perspective settings produced an invalid transformation.");
    }
    [a[col], a[pivot]] = [a[pivot], a[col]];
    const divisor = a[col][col];
    for (let j = col; j <= 8; j++) a[col][j] /= divisor;
    for (let row = 0; row < 8; row++) {
      if (row === col) continue;
      const factor = a[row][col];
      for (let j = col; j <= 8; j++) a[row][j] -= factor * a[col][j];
    }
  }
  return a.map((row) => row[8]);
}

/**
 * Build the inverse perspective mapping in normalized coordinates.
 * Corners are mapped from destination into source, so samples never need
 * approximate forward-splat interpolation.
 */
function inverseHomography(settings) {
  const angle = settings.rotation * Math.PI / 180;
  const cosine = Math.cos(angle);
  const sine = Math.sin(angle);
  const p = settings.perspective_vertical;
  const q = settings.perspective_horizontal;
  const corners = [
    [-1, -1], [1, -1], [1, 1], [-1, 1]
  ];
  const destination = corners.map(([x, y]) => {
    const warpedX = x * (1 + p * y);
    const warpedY = y * (1 + q * x);
    return [
      warpedX * cosine - warpedY * sine,
      warpedX * sine + warpedY * cosine
    ];
  });
  const matrix = [];
  const rhs = [];
  destination.forEach(([x, y], index) => {
    const [u, v] = corners[index];
    matrix.push([x, y, 1, 0, 0, 0, -u * x, -u * y]);
    rhs.push(u);
    matrix.push([0, 0, 0, x, y, 1, -v * x, -v * y]);
    rhs.push(v);
  });
  return solve8(matrix, rhs);
}

export function geometryIsNeutral(input = {}) {
  const s = validatedSpatial(input);
  return s.rotation === 0
    && s.perspective_vertical === 0
    && s.perspective_horizontal === 0
    && !s.flip_horizontal && !s.flip_vertical;
}

/**
 * Bilinear RGBA sampling, with transparent pixels beyond the original image.
 * Geometry keeps the original canvas dimensions to avoid unexpected cropping.
 */
export function transformPixels(source, width, height, input = {}) {
  if (!Number.isInteger(width) || !Number.isInteger(height)
    || width <= 0 || height <= 0 || source?.length !== 4 * width * height) {
    throw new Error("Invalid RGBA image dimensions.");
  }
  const s = validatedSpatial(input);
  if (geometryIsNeutral(s)) return new Uint8ClampedArray(source);

  const matrix = inverseHomography(s);
  const result = new Uint8ClampedArray(source.length);
  for (let y = 0; y < height; y++) {
    const v = height === 1 ? 0 : (2 * y / (height - 1) - 1);
    for (let x = 0; x < width; x++) {
      const u = width === 1 ? 0 : (2 * x / (width - 1) - 1);
      const scale = matrix[6] * u + matrix[7] * v + 1;
      if (Math.abs(scale) < 1e-9) continue;
      let sourceU = (matrix[0] * u + matrix[1] * v + matrix[2]) / scale;
      let sourceV = (matrix[3] * u + matrix[4] * v + matrix[5]) / scale;

      if (s.flip_horizontal) sourceU = -sourceU;
      if (s.flip_vertical) sourceV = -sourceV;

      const sx = width === 1 ? 0 : (sourceU + 1) * (width - 1) / 2;
      const sy = height === 1 ? 0 : (sourceV + 1) * (height - 1) / 2;
      if (sx < -0.001 || sx > width - 1 + 0.001
        || sy < -0.001 || sy > height - 1 + 0.001) {
        continue;
      }
      const clampedX = clamp(sx, 0, width - 1);
      const clampedY = clamp(sy, 0, height - 1);
      const x0 = Math.floor(clampedX);
      const y0 = Math.floor(clampedY);
      const x1 = Math.min(width - 1, x0 + 1);
      const y1 = Math.min(height - 1, y0 + 1);
      const fx = clampedX - x0;
      const fy = clampedY - y0;
      const weights = [
        (1 - fx) * (1 - fy),
        fx * (1 - fy),
        (1 - fx) * fy,
        fx * fy
      ];
      const indices = [
        (y0 * width + x0) * 4,
        (y0 * width + x1) * 4,
        (y1 * width + x0) * 4,
        (y1 * width + x1) * 4
      ];
      const out = (y * width + x) * 4;
      let alpha = 0;
      for (let k = 0; k < 4; k++) alpha += source[indices[k] + 3] * weights[k];
      result[out + 3] = alpha;
      if (!alpha) continue;
      // Interpolate premultiplied RGBA to avoid dark halos at transparent edges.
      for (let channel = 0; channel < 3; channel++) {
        let value = 0;
        for (let k = 0; k < 4; k++) {
          value += source[indices[k] + channel]
            * source[indices[k] + 3] * weights[k];
        }
        result[out + channel] = clamp(Math.round(value / alpha), 0, 255);
      }
    }
  }
  return result;
}

/**
 * Returns 0 (no local effect) to 1 (full local effect) for a pixel.
 * Coordinates are normalized against output image dimensions.
 */
export function gradientWeight(x, y, input = {}) {
  const s = validatedSpatial(input);
  const dx = s.gradient_full_x - s.gradient_zero_x;
  const dy = s.gradient_full_y - s.gradient_zero_y;
  const length2 = dx * dx + dy * dy;
  if (length2 < 1e-8) return 0;
  let raw;
  if (s.gradient_type === "radial") {
    const radius = Math.sqrt(length2);
    raw = 1 - Math.hypot(x - s.gradient_full_x, y - s.gradient_full_y) / radius;
  } else {
    raw = ((x - s.gradient_zero_x) * dx
      + (y - s.gradient_zero_y) * dy) / length2;
  }
  const t = clamp(raw, 0, 1);
  return t * t * (3 - 2 * t);
}

/**
 * Apply regional exposure and saturation after global corrections.
 * A gradient affects only output pixels with nonzero alpha.
 */
export function applyGradient(source, width, height, input = {}) {
  const s = validatedSpatial(input);
  if (!s.gradient_enabled
    || (s.gradient_exposure === 0 && s.gradient_saturation === 1)) {
    return new Uint8ClampedArray(source);
  }
  const output = new Uint8ClampedArray(source);
  // Compute coefficients once, rather than validating per-pixel settings.
  const dx = s.gradient_full_x - s.gradient_zero_x;
  const dy = s.gradient_full_y - s.gradient_zero_y;
  const distanceSquared = dx * dx + dy * dy;
  if (distanceSquared < 1e-8) return output;

  for (let y = 0; y < height; y++) {
    const v = height === 1 ? 0.5 : y / (height - 1);
    for (let x = 0; x < width; x++) {
      const u = width === 1 ? 0.5 : x / (width - 1);
      const i = (y * width + x) * 4;
      if (source[i + 3] === 0) continue;
      let t;
      if (s.gradient_type === "radial") {
        t = 1 - Math.hypot(u - s.gradient_full_x, v - s.gradient_full_y)
          / Math.sqrt(distanceSquared);
      } else {
        t = ((u - s.gradient_zero_x) * dx
          + (v - s.gradient_zero_y) * dy) / distanceSquared;
      }
      t = clamp(t, 0, 1);
      const weight = t * t * (3 - 2 * t);
      if (weight === 0) continue;

      const exposureFactor = 2 ** (s.gradient_exposure * weight);
      const saturationFactor = 1 + (s.gradient_saturation - 1) * weight;
      const red = source[i];
      const green = source[i + 1];
      const blue = source[i + 2];
      const luminance = 0.299 * red + 0.587 * green + 0.114 * blue;
      output[i] = clamp(Math.round(
        (luminance + (red - luminance) * saturationFactor) * exposureFactor
      ), 0, 255);
      output[i + 1] = clamp(Math.round(
        (luminance + (green - luminance) * saturationFactor) * exposureFactor
      ), 0, 255);
      output[i + 2] = clamp(Math.round(
        (luminance + (blue - luminance) * saturationFactor) * exposureFactor
      ), 0, 255);
    }
  }
  return output;
}

export function correctImage(source, width, height, colorSettings = {}, spatialSettings = {}) {
  const before = transformPixels(source, width, height, spatialSettings);
  const globallyCorrected = correctPixels(before, width, height, colorSettings);
  return {
    before,
    after: applyGradient(globallyCorrected, width, height, spatialSettings)
  };
}
