/**
 * PicVidFix Web: browser-only RGBA color corrections.
 *
 * Correction stages follow the ordering of color_pipeline.py. Browser Canvas
 * color conversions and rounding can differ slightly from OpenCV. Both browser
 * preview and browser export call THIS function, avoiding diverging pipelines.
 *
 * No network access, packages, or backend is required.
 */

export const DEFAULTS = Object.freeze({
  brightness: 0,
  contrast: 1,
  exposure: 0,
  saturation: 1,
  vibrance: 0,
  highlights: 0,
  shadows: 0,
  gamma: 1,
  sharpness: 0,
  blue_gain: 1,
  green_gain: 1,
  red_gain: 1,
  remap_enabled: false,
  remap_hex: "FF8800",
  hue_low: 95,
  hue_high: 135
});

export const CONTROLS = Object.freeze([
  { id: "brightness", label: "Brightness", min: -255, max: 255, step: 0.1 },
  { id: "contrast", label: "Contrast", min: 0, max: 3, step: 0.01 },
  { id: "exposure", label: "Exposure (EV)", min: -3, max: 3, step: 0.01 },
  { id: "saturation", label: "Saturation", min: 0, max: 3, step: 0.01 },
  { id: "vibrance", label: "Vibrance", min: -1, max: 1, step: 0.01 },
  { id: "highlights", label: "Highlights", min: -5, max: 5, step: 0.01 },
  { id: "shadows", label: "Shadows", min: -5, max: 5, step: 0.01 },
  { id: "gamma", label: "Gamma", min: 0.2, max: 3, step: 0.01 },
  { id: "sharpness", label: "Sharpness", min: 0, max: 2, step: 0.01 },
  { id: "blue_gain", label: "Blue gain", min: 0, max: 8, step: 0.001 },
  { id: "green_gain", label: "Green gain", min: 0, max: 8, step: 0.001 },
  { id: "red_gain", label: "Red gain", min: 0, max: 8, step: 0.001 },
  { id: "hue_low", label: "Blue hue low", min: 0, max: 179, step: 1 },
  { id: "hue_high", label: "Blue hue high", min: 0, max: 179, step: 1 }
]);

const CONTROL_BY_ID = new Map(CONTROLS.map((control) => [control.id, control]));

function clamp(value, low, high) {
  return Math.min(high, Math.max(low, value));
}

function byte(value) {
  return Math.trunc(clamp(value, 0, 255));
}

function reflected(index, size) {
  if (size <= 1) return 0;
  while (index < 0 || index >= size) {
    index = index < 0 ? -index : 2 * size - index - 2;
  }
  return index;
}

export function parseHex(hex) {
  if (typeof hex !== "string" || !/^#?[0-9a-f]{6}$/i.test(hex.trim())) {
    throw new Error("Color must be six hexadecimal digits, e.g. FF8800.");
  }
  const value = hex.trim().replace("#", "");
  return [
    Number.parseInt(value.slice(0, 2), 16),
    Number.parseInt(value.slice(2, 4), 16),
    Number.parseInt(value.slice(4, 6), 16)
  ];
}

export function validatedSettings(input = {}) {
  if (!input || typeof input !== "object" || Array.isArray(input)) {
    throw new Error("Invalid correction preset.");
  }
  const result = { ...DEFAULTS };
  for (const control of CONTROLS) {
    if (input[control.id] === undefined) continue;
    const value = Number(input[control.id]);
    if (!Number.isFinite(value) || value < control.min || value > control.max) {
      throw new Error(control.label + " must be between " + control.min + " and " + control.max + ".");
    }
    result[control.id] = control.step === 1 ? Math.round(value) : value;
  }
  result.remap_enabled = Boolean(input.remap_enabled ?? DEFAULTS.remap_enabled);
  result.remap_hex = String(input.remap_hex ?? DEFAULTS.remap_hex).replace("#", "").toUpperCase();
  parseHex(result.remap_hex);
  if (result.hue_low > result.hue_high) {
    throw new Error("Blue hue low cannot exceed blue hue high.");
  }
  return result;
}

function toHSV(r, g, b) {
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const delta = max - min;
  let degrees = 0;
  if (delta) {
    if (max === r) degrees = 60 * (((g - b) / delta) % 6);
    else if (max === g) degrees = 60 * ((b - r) / delta + 2);
    else degrees = 60 * ((r - g) / delta + 4);
  }
  if (degrees < 0) degrees += 360;
  return [
    Math.min(179, Math.round(degrees / 2)),
    max === 0 ? 0 : Math.round((delta / max) * 255),
    max
  ];
}

function fromHSV(h, s, v) {
  const degrees = h * 2;
  const saturation = s / 255;
  const value = v / 255;
  const chroma = value * saturation;
  const part = chroma * (1 - Math.abs(((degrees / 60) % 2) - 1));
  const offset = value - chroma;
  let r = 0;
  let g = 0;
  let b = 0;
  if (degrees < 60) [r, g, b] = [chroma, part, 0];
  else if (degrees < 120) [r, g, b] = [part, chroma, 0];
  else if (degrees < 180) [r, g, b] = [0, chroma, part];
  else if (degrees < 240) [r, g, b] = [0, part, chroma];
  else if (degrees < 300) [r, g, b] = [part, 0, chroma];
  else [r, g, b] = [chroma, 0, part];
  return [
    clamp(Math.round((r + offset) * 255), 0, 255),
    clamp(Math.round((g + offset) * 255), 0, 255),
    clamp(Math.round((b + offset) * 255), 0, 255)
  ];
}

function blurScalar(values, width, height, weights) {
  const radius = Math.floor(weights.length / 2);
  const horizontal = new Float32Array(values.length);
  const result = new Float32Array(values.length);

  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      let sum = 0;
      for (let k = -radius; k <= radius; k++) {
        sum += values[y * width + reflected(x + k, width)] * weights[k + radius];
      }
      horizontal[y * width + x] = sum;
    }
  }
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      let sum = 0;
      for (let k = -radius; k <= radius; k++) {
        sum += horizontal[reflected(y + k, height) * width + x] * weights[k + radius];
      }
      result[y * width + x] = sum;
    }
  }
  return result;
}

/**
 * Process RGBA pixels and return a new RGBA array. The source remains intact.
 * The same exact function is used for preview and final image export.
 */
export function correctPixels(source, width, height, input = {}) {
  if (!Number.isInteger(width) || !Number.isInteger(height)
      || width <= 0 || height <= 0 || source?.length !== width * height * 4) {
    throw new Error("Invalid RGBA image dimensions.");
  }
  const s = validatedSettings(input);
  const result = new Uint8ClampedArray(source);

  const neutral = Object.keys(DEFAULTS).every((key) =>
    s[key] === DEFAULTS[key] || (key === "remap_hex" && !s.remap_enabled)
      || (key === "hue_low" && !s.remap_enabled)
      || (key === "hue_high" && !s.remap_enabled)
  );
  if (neutral) return result;

  const pixelCount = width * height;
  const gains = [s.red_gain, s.green_gain, s.blue_gain];
  for (let i = 0; i < result.length; i += 4) {
    result[i] = byte(Math.floor(source[i] * gains[0]));
    result[i + 1] = byte(Math.floor(source[i + 1] * gains[1]));
    result[i + 2] = byte(Math.floor(source[i + 2] * gains[2]));
  }

  if (s.remap_enabled) {
    const target = parseHex(s.remap_hex);
    const total = target[0] + target[1] + target[2];
    const ratio = total ? target.map((channel) => channel / total) : [0, 0, 0];
    const mask = new Float32Array(pixelCount);
    for (let n = 0; n < pixelCount; n++) {
      const i = n * 4;
      const [h] = toHSV(result[i], result[i + 1], result[i + 2]);
      mask[n] = Number(h >= s.hue_low && h <= s.hue_high);
    }
    const feather = blurScalar(mask, width, height, [0.25, 0.5, 0.25]);
    for (let n = 0; n < pixelCount; n++) {
      const i = n * 4;
      const alpha = feather[n];
      const brightness = Math.max(result[i], result[i + 1], result[i + 2]);
      for (let channel = 0; channel < 3; channel++) {
        result[i + channel] = byte(
          result[i + channel] * (1 - alpha)
          + brightness * ratio[channel] * 3 * alpha
        );
      }
    }
  }

  const hsvNeeded = s.exposure !== 0 || s.saturation !== 1 || s.vibrance !== 0;
  const luminanceNeeded = s.highlights !== 0 || s.shadows !== 0;
  const exposureFactor = 2 ** s.exposure;
  const gammaLut = new Uint8Array(256);
  if (s.gamma !== 1) {
    for (let n = 0; n < 256; n++) {
      gammaLut[n] = byte(((n / 255) ** (1 / s.gamma)) * 255);
    }
  }

  for (let i = 0; i < result.length; i += 4) {
    const channels = [
      127.5 + s.contrast * (result[i] + s.brightness - 127.5),
      127.5 + s.contrast * (result[i + 1] + s.brightness - 127.5),
      127.5 + s.contrast * (result[i + 2] + s.brightness - 127.5)
    ];

    if (hsvNeeded) {
      const [h, originalS, originalV] = toHSV(
        byte(channels[0]), byte(channels[1]), byte(channels[2])
      );
      let saturation = originalS * s.saturation;
      saturation += s.vibrance * ((255 - saturation) / 255) * 50;
      const value = originalV * exposureFactor;
      const rgb = fromHSV(h, byte(saturation), byte(value));
      channels[0] = rgb[0];
      channels[1] = rgb[1];
      channels[2] = rgb[2];
    }

    if (luminanceNeeded) {
      const gray = Math.round(
        0.299 * byte(channels[0])
        + 0.587 * byte(channels[1])
        + 0.114 * byte(channels[2])
      ) / 255;
      const highlightMask = clamp((gray - 0.5) * 2, 0, 1) ** 2;
      const shadowMask = clamp((0.5 - gray) * 2, 0, 1) ** 2;
      const delta = 25 * (
        s.highlights * highlightMask + s.shadows * shadowMask
      );
      for (let channel = 0; channel < 3; channel++) {
        channels[channel] += delta;
      }
    }

    for (let channel = 0; channel < 3; channel++) {
      let value = byte(channels[channel]);
      if (s.gamma !== 1) value = gammaLut[value];
      result[i + channel] = value;
    }
  }

  if (s.sharpness > 0) {
    const weights = [0.05448868, 0.24420134, 0.40261996, 0.24420134, 0.05448868];
    for (let channel = 0; channel < 3; channel++) {
      const plane = new Float32Array(pixelCount);
      for (let n = 0; n < pixelCount; n++) {
        plane[n] = result[4 * n + channel];
      }
      const blurred = blurScalar(plane, width, height, weights);
      for (let n = 0; n < pixelCount; n++) {
        const i = 4 * n + channel;
        result[i] = clamp(Math.round(
          (1 + s.sharpness) * result[i] - s.sharpness * blurred[n]
        ), 0, 255);
      }
    }
  }

  return result;
}

export function measureNeutralPatch(source, width, height, rectangle, targetHex = "FFFFFF") {
  if (source?.length !== width * height * 4) {
    throw new Error("Invalid source image.");
  }
  const [x1, y1, x2, y2] = rectangle;
  const left = clamp(Math.floor(Math.min(x1, x2)), 0, width);
  const right = clamp(Math.ceil(Math.max(x1, x2)), 0, width);
  const top = clamp(Math.floor(Math.min(y1, y2)), 0, height);
  const bottom = clamp(Math.ceil(Math.max(y1, y2)), 0, height);
  if (right <= left || bottom <= top) {
    throw new Error("Select a larger neutral area.");
  }
  const sum = [0, 0, 0];
  let count = 0;
  for (let y = top; y < bottom; y++) {
    for (let x = left; x < right; x++) {
      const i = (y * width + x) * 4;
      for (let channel = 0; channel < 3; channel++) sum[channel] += source[i + channel];
      count++;
    }
  }
  const target = parseHex(targetHex);
  const gains = target.map((value, channel) => value / clamp(sum[channel] / count, 1, 255));
  return { red_gain: gains[0], green_gain: gains[1], blue_gain: gains[2] };
}

export function isControlValueValid(id, value) {
  const spec = CONTROL_BY_ID.get(id);
  return Boolean(spec && Number.isFinite(value) && value >= spec.min && value <= spec.max);
}
