/**
 * PicVidFix Web v0.1: standalone photo editing frontend.
 * Files stay local. There are no uploads, API calls, or third-party scripts.
 */

import {
  CONTROLS,
  DEFAULTS,
  correctPixels,
  isControlValueValid,
  measureNeutralPatch,
  validatedSettings
} from "./color-engine.js";
import {
  SPATIAL_DEFAULTS,
  SPATIAL_CONTROLS,
  validatedSpatial
} from "./spatial-engine.js";

const $ = (id) => document.getElementById(id);
const elements = {
  fileInput: $("fileInput"),
  presetInput: $("presetInput"),
  original: $("originalCanvas"),
  corrected: $("correctedCanvas"),
  selection: $("selectionCanvas"),
  gradientOverlay: $("gradientCanvas"),
  stage: $("stage"),
  emptyState: $("emptyState"),
  splitLine: $("splitLine"),
  compareSlider: $("compareSlider"),
  compareValue: $("compareValue"),
  filename: $("filename"),
  dimensions: $("dimensions"),
  status: $("status"),
  download: $("downloadButton"),
  sample: $("sampleButton"),
  remapEnabled: $("remapEnabled"),
  remapHex: $("remapHex"),
  calibrationHex: $("calibrationHex"),
  toast: $("toast")
};

const state = {
  settings: { ...DEFAULTS },
  spatial: { ...SPATIAL_DEFAULTS },
  showGradientHandles: true,
  bitmap: null,
  filename: null,
  width: 0,
  height: 0,
  previewWidth: 0,
  previewHeight: 0,
  sourcePreview: null,
  previewWorker: null,
  previewInFlight: false,
  previewWanted: false,
  previewTimer: null,
  previewJobId: 0,
  busyExporting: false,
  selectingPatch: false
};

const controls = new Map();
const spatialControls = new Map();

function status(message, error = false) {
  elements.status.textContent = message;
  elements.status.classList.toggle("error", error);
}

let toastTimer;
function toast(message) {
  elements.toast.textContent = message;
  elements.toast.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { elements.toast.hidden = true; }, 3600);
}

function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 30000);
}

function settingsSnapshot() {
  return validatedSettings(state.settings);
}

function spatialSnapshot() {
  return validatedSpatial(state.spatial);
}

function formatValue(id, value) {
  if (id.startsWith("hue_")) return String(Math.round(value));
  return String(Number(value.toFixed(5)));
}

function syncControl(id) {
  const elementsForControl = controls.get(id);
  if (!elementsForControl) return;
  const value = state.settings[id];
  elementsForControl.range.value = String(value);
  elementsForControl.number.value = formatValue(id, value);
  elementsForControl.number.classList.remove("invalid");
}

function syncAllControls() {
  for (const { id } of CONTROLS) syncControl(id);
  elements.remapEnabled.checked = state.settings.remap_enabled;
  elements.remapHex.value = state.settings.remap_hex;
  syncSpatialControls();
}

function resetAll() {
  state.settings = { ...DEFAULTS };
  state.spatial = { ...SPATIAL_DEFAULTS };
  elements.calibrationHex.value = "FFFFFF";
  syncAllControls();
  schedulePreview();
  status("All adjustments reset. The original image is unchanged.");
}

function buildControls() {
  const placements = {
    toneControls: [
      "brightness", "contrast", "exposure", "highlights", "shadows", "gamma"
    ],
    colorControls: ["saturation", "vibrance", "sharpness"],
    gainControls: ["red_gain", "green_gain", "blue_gain"],
    hueControls: ["hue_low", "hue_high"]
  };

  for (const [containerId, ids] of Object.entries(placements)) {
    for (const id of ids) {
      const spec = CONTROLS.find((item) => item.id === id);
      const wrapper = document.createElement("div");
      wrapper.className = "control-item";

      const title = document.createElement("div");
      title.className = "control-title";
      const label = document.createElement("label");
      label.textContent = spec.label;
      label.htmlFor = "number-" + id;
      const reset = document.createElement("button");
      reset.type = "button";
      reset.textContent = "Reset";
      reset.setAttribute("aria-label", "Reset " + spec.label);
      reset.addEventListener("click", () => {
        state.settings[id] = DEFAULTS[id];
        syncControl(id);
        schedulePreview();
      });
      title.append(label, reset);

      const inputs = document.createElement("div");
      inputs.className = "control-inputs";
      const range = document.createElement("input");
      range.type = "range";
      range.min = String(spec.min);
      range.max = String(spec.max);
      range.step = String(spec.step);
      range.value = String(DEFAULTS[id]);
      range.setAttribute("aria-label", spec.label + " slider");

      const number = document.createElement("input");
      number.type = "number";
      number.id = "number-" + id;
      number.min = String(spec.min);
      number.max = String(spec.max);
      // The text field intentionally allows greater precision than the slider.
      number.step = "any";
      number.value = formatValue(id, DEFAULTS[id]);
      number.setAttribute("aria-label", spec.label + " exact value");

      range.addEventListener("input", () => {
        state.settings[id] = Number(range.value);
        number.value = formatValue(id, state.settings[id]);
        number.classList.remove("invalid");
        schedulePreview();
      });

      number.addEventListener("input", () => {
        // Preserve partially typed strings, e.g. "-" and "1.", while editing.
        const value = Number(number.value);
        const valid = number.value.trim() !== "" && isControlValueValid(id, value);
        number.classList.toggle("invalid", !valid);
        if (!valid) return;
        state.settings[id] = value;
        range.value = String(value);
        schedulePreview();
      });

      number.addEventListener("blur", () => {
        // Invalid/incomplete edits revert; valid fractional values are retained.
        syncControl(id);
      });

      inputs.append(range, number);
      wrapper.append(title, inputs);
      $(containerId).append(wrapper);
      controls.set(id, { range, number });
    }
  }
}

function updateSplit() {
  const value = Number(elements.compareSlider.value);
  elements.corrected.style.clipPath = "inset(0 0 0 " + value + "%)";
  elements.splitLine.style.left = value + "%";
  elements.compareValue.value = value + "%";
}

function ensurePreviewWorker() {
  if (state.previewWorker) return;
  state.previewWorker = new Worker(
    new URL("./worker.js", import.meta.url), { type: "module" }
  );
  state.previewWorker.onmessage = ({ data }) => {
    state.previewInFlight = false;
    if (data.error) {
      status("Preview error: " + data.error, true);
    } else if (!state.previewWanted && data.id === state.previewJobId
        && data.width === state.previewWidth
        && data.height === state.previewHeight) {
      const ctx = elements.corrected.getContext("2d", { willReadFrequently: false });
      ctx.putImageData(
        new ImageData(new Uint8ClampedArray(data.buffer), data.width, data.height),
        0, 0
      );
      if (data.beforeBuffer) {
        elements.original.getContext("2d").putImageData(
          new ImageData(
            new Uint8ClampedArray(data.beforeBuffer), data.width, data.height
          ), 0, 0
        );
      }
      status("Preview updated. Local corrections and geometry are included in export.");
    }
    if (state.previewWanted) dispatchPreview();
  };
  state.previewWorker.onerror = () => {
    state.previewInFlight = false;
    status("Preview worker failed. Reload the page to retry.", true);
  };
}

function schedulePreview() {
  if (!state.sourcePreview) return;
  state.previewWanted = true;
  clearTimeout(state.previewTimer);
  state.previewTimer = setTimeout(dispatchPreview, 70);
}

function dispatchPreview() {
  if (!state.sourcePreview || !state.previewWanted || state.previewInFlight) return;
  let settings;
  let spatial;
  try {
    settings = settingsSnapshot();
    spatial = spatialSnapshot();
  } catch (error) {
    status(error.message, true);
    return;
  }
  ensurePreviewWorker();
  state.previewWanted = false;
  state.previewInFlight = true;
  const id = ++state.previewJobId;
  const copy = new Uint8ClampedArray(state.sourcePreview);
  state.previewWorker.postMessage({
    id,
    width: state.previewWidth,
    height: state.previewHeight,
    buffer: copy.buffer,
    settings,
    spatial,
    mode: "preview"
  }, [copy.buffer]);
  status("Updating preview...");
}

function loadBitmap(bitmap, filename) {
  const width = bitmap.width;
  const height = bitmap.height;
  if (width * height > 24_000_000) {
    bitmap.close?.();
    throw new Error(
      "This image exceeds the 24-megapixel browser safety limit. Try the desktop version."
    );
  }

  const previous = state.bitmap;
  state.bitmap = bitmap;
  previous?.close?.();
  state.filename = filename;
  state.width = width;
  state.height = height;

  const previewScale = Math.min(1, 1150 / width, 820 / height);
  const previewWidth = Math.max(1, Math.round(width * previewScale));
  const previewHeight = Math.max(1, Math.round(height * previewScale));
  state.previewWidth = previewWidth;
  state.previewHeight = previewHeight;

  for (const canvas of [
    elements.original, elements.corrected, elements.selection, elements.gradientOverlay
  ]) {
    canvas.width = previewWidth;
    canvas.height = previewHeight;
  }

  const ctx = elements.original.getContext("2d", { willReadFrequently: true });
  ctx.clearRect(0, 0, previewWidth, previewHeight);
  ctx.drawImage(bitmap, 0, 0, previewWidth, previewHeight);
  state.sourcePreview = ctx.getImageData(0, 0, previewWidth, previewHeight).data;
  elements.corrected.getContext("2d").drawImage(
    elements.original, 0, 0, previewWidth, previewHeight
  );

  state.selectingPatch = false;
  elements.selection.hidden = true;
  elements.sample.textContent = "Select neutral patch";
  updateGradientOverlay();

  elements.stage.hidden = false;
  elements.emptyState.hidden = true;
  elements.stage.style.aspectRatio = width + " / " + height;
  elements.stage.style.maxWidth = Math.round(620 * width / height) + "px";

  elements.filename.textContent = filename;
  elements.dimensions.textContent = width.toLocaleString() + " × "
    + height.toLocaleString() + " px · processed locally";
  elements.download.disabled = false;
  elements.sample.disabled = false;
  schedulePreview();
}

async function loadFile(file) {
  if (!file) return;
  if (file.type.startsWith("video/") || /\.(mp4|mov|avi|mkv|webm|m4v)$/i.test(file.name)) {
    status("Video editing is coming to the browser version. Try the desktop edition below.", true);
    $("desktopNote").scrollIntoView({ behavior: "smooth", block: "nearest" });
    toast("Video is not supported in Web v0.1. Use PicVidFix Desktop.");
    return;
  }
  if (!file.type.startsWith("image/")) {
    status("Please choose a supported image (JPG, PNG, WebP, BMP, GIF or AVIF).", true);
    return;
  }
  if (file.size > 75 * 1024 * 1024) {
    status("File is over 75 MB. Try the desktop application for large originals.", true);
    return;
  }
  status("Opening " + file.name + "...");
  try {
    const bitmap = await createImageBitmap(file);
    loadBitmap(bitmap, file.name);
  } catch (error) {
    status("Could not open photo: " + error.message, true);
  }
}

function makeDemoCanvas() {
  const canvas = document.createElement("canvas");
  canvas.width = 1080;
  canvas.height = 700;
  const ctx = canvas.getContext("2d");

  const sky = ctx.createLinearGradient(0, 0, 0, 700);
  sky.addColorStop(0, "#162d51");
  sky.addColorStop(0.47, "#59817d");
  sky.addColorStop(1, "#bbaa7a");
  ctx.fillStyle = sky;
  ctx.fillRect(0, 0, 1080, 700);

  const sunlight = ctx.createRadialGradient(790, 190, 22, 790, 190, 340);
  sunlight.addColorStop(0, "rgba(255,225,152,.85)");
  sunlight.addColorStop(1, "rgba(255,225,152,0)");
  ctx.fillStyle = sunlight;
  ctx.fillRect(400, 0, 680, 550);
  ctx.fillStyle = "#f5cb8d";
  ctx.beginPath(); ctx.arc(785, 180, 88, 0, 2 * Math.PI); ctx.fill();

  ctx.fillStyle = "#28404c";
  ctx.beginPath(); ctx.moveTo(0, 420); ctx.lineTo(195, 260); ctx.lineTo(420, 415);
  ctx.lineTo(685, 285); ctx.lineTo(880, 440); ctx.lineTo(1080, 365);
  ctx.lineTo(1080, 700); ctx.lineTo(0, 700); ctx.fill();

  ctx.fillStyle = "#162a37";
  ctx.beginPath(); ctx.moveTo(0, 570); ctx.lineTo(240, 455); ctx.lineTo(455, 580);
  ctx.lineTo(730, 470); ctx.lineTo(1080, 615); ctx.lineTo(1080, 700); ctx.lineTo(0, 700);
  ctx.fill();

  // A backlit portrait silhouette supplies a useful exposure test subject.
  ctx.save();
  ctx.translate(535, 435);
  ctx.fillStyle = "#182b35";
  ctx.beginPath(); ctx.ellipse(0, 250, 183, 190, 0, 0, Math.PI * 2); ctx.fill();
  ctx.fillStyle = "#65534c";
  ctx.beginPath(); ctx.ellipse(0, 5, 87, 118, 0, 0, Math.PI * 2); ctx.fill();
  ctx.fillStyle = "#283441";
  ctx.beginPath(); ctx.ellipse(0, -63, 90, 66, 0, Math.PI, 2 * Math.PI); ctx.fill();
  ctx.fillStyle = "#9e7a65";
  ctx.fillRect(-40, 110, 80, 50);
  ctx.fillStyle = "#c49b75";
  ctx.beginPath(); ctx.ellipse(-29, 8, 14, 10, 0, 0, 2 * Math.PI); ctx.fill();
  ctx.beginPath(); ctx.ellipse(29, 8, 14, 10, 0, 0, 2 * Math.PI); ctx.fill();
  ctx.restore();

  for (let i = 0; i < 38; i++) {
    const x = (i * 167 + 27) % 1080;
    const y = 535 + (i * 113) % 165;
    ctx.fillStyle = i % 3 === 0 ? "#526b64" : "#354e4c";
    ctx.fillRect(x, y, 2 + (i % 3), 10 + (i % 20));
  }
  return canvas;
}

async function loadDemo() {
  try {
    const bitmap = await createImageBitmap(makeDemoCanvas());
    loadBitmap(bitmap, "PicVidFix_sample.png");
    toast("Demo photo loaded. Try exposure, highlights, or contrast.");
  } catch (error) {
    status("Could not load demo: " + error.message, true);
  }
}

function savePreset() {
  try {
    const payload = {
      schema: "picvidfix-web-preset",
      version: 1,
      settings: settingsSnapshot(),
      spatial: spatialSnapshot()
    };
    downloadBlob(
      new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" }),
      "picvidfix-preset.json"
    );
    toast("Preset saved as JSON.");
  } catch (error) {
    status("Could not save preset: " + error.message, true);
  }
}

async function loadPreset(file) {
  if (!file) return;
  try {
    if (file.size > 100_000) throw new Error("Preset JSON is unexpectedly large.");
    const raw = JSON.parse(await file.text());
    const incoming = raw.settings || raw;
    const converted = { ...incoming };

    // Import presets made by the existing Python desktop version.
    if (Array.isArray(incoming.offset) && incoming.offset.length === 3) {
      converted.blue_gain = incoming.offset[0];
      converted.green_gain = incoming.offset[1];
      converted.red_gain = incoming.offset[2];
    }
    if (Array.isArray(incoming.blue_range) && incoming.blue_range.length === 2) {
      converted.hue_low = incoming.blue_range[0];
      converted.hue_high = incoming.blue_range[1];
    }
    if (incoming.remap_blue) {
      converted.remap_enabled = true;
      converted.remap_hex = incoming.remap_blue;
    } else if (incoming.remap_blue === null) {
      converted.remap_enabled = false;
      converted.remap_hex = raw.remap_hex || DEFAULTS.remap_hex;
    }

    const spatial = validatedSpatial(raw.spatial ?? SPATIAL_DEFAULTS);
    state.settings = validatedSettings(converted);
    state.spatial = spatial;
    syncAllControls();
    schedulePreview();
    toast("Preset loaded. Existing image retained.");
  } catch (error) {
    status("Could not load preset: " + error.message, true);
  }
}

function setPatchSelection(enabled) {
  state.selectingPatch = enabled;
  elements.selection.hidden = !enabled;
  elements.sample.textContent = enabled ? "Cancel selection" : "Select neutral patch";
  updateGradientOverlay();
  const context = elements.selection.getContext("2d");
  context.clearRect(0, 0, state.previewWidth, state.previewHeight);
  if (enabled) status("Drag a rectangle over a neutral area of the photo.");
}

function pointerPosition(event) {
  const rect = elements.selection.getBoundingClientRect();
  return [
    Math.max(0, Math.min(state.previewWidth,
      (event.clientX - rect.left) * state.previewWidth / rect.width)),
    Math.max(0, Math.min(state.previewHeight,
      (event.clientY - rect.top) * state.previewHeight / rect.height))
  ];
}

function installPatchSelection() {
  let start = null;
  elements.selection.addEventListener("pointerdown", (event) => {
    if (!state.selectingPatch) return;
    start = pointerPosition(event);
    elements.selection.setPointerCapture(event.pointerId);
  });
  elements.selection.addEventListener("pointermove", (event) => {
    if (!start) return;
    const point = pointerPosition(event);
    const context = elements.selection.getContext("2d");
    context.clearRect(0, 0, state.previewWidth, state.previewHeight);
    context.strokeStyle = "#9cfdd0";
    context.lineWidth = 3;
    context.strokeRect(
      start[0], start[1], point[0] - start[0], point[1] - start[1]
    );
  });
  elements.selection.addEventListener("pointerup", (event) => {
    if (!start) return;
    const end = pointerPosition(event);
    try {
      const measured = measureNeutralPatch(
        state.sourcePreview, state.previewWidth, state.previewHeight,
        [...start, ...end], elements.calibrationHex.value
      );
      let limited = false;
      for (const [id, gain] of Object.entries(measured)) {
        const safe = Math.min(gain, 8);
        if (safe !== gain) limited = true;
        state.settings[id] = safe;
        syncControl(id);
      }
      schedulePreview();
      toast(limited
        ? "Calibration applied. A channel was limited to 8× gain."
        : "Neutral patch calibrated. RGB gains updated.");
    } catch (error) {
      status("Calibration: " + error.message, true);
    }
    start = null;
    setPatchSelection(false);
  });
  elements.selection.addEventListener("pointercancel", () => {
    start = null;
    setPatchSelection(false);
  });
}

async function exportPNG() {
  if (!state.bitmap || state.busyExporting) return;
  let settings;
  let spatial;
  try {
    settings = settingsSnapshot();
    spatial = spatialSnapshot();
  } catch (error) {
    status("Export settings error: " + error.message, true);
    return;
  }

  state.busyExporting = true;
  elements.download.disabled = true;
  status("Preparing full-resolution image...");

  const worker = new Worker(new URL("./worker.js", import.meta.url), { type: "module" });
  const finish = () => {
    worker.terminate();
    state.busyExporting = false;
    elements.download.disabled = !state.bitmap;
  };

  try {
    // Allow the browser to paint the status before potentially heavy canvas IO.
    await new Promise((resolve) => setTimeout(resolve, 0));
    const width = state.width;
    const height = state.height;
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const context = canvas.getContext("2d", { willReadFrequently: true });
    context.drawImage(state.bitmap, 0, 0);
    const imageData = context.getImageData(0, 0, width, height);

    worker.onmessage = ({ data }) => {
      if (data.error) {
        status("Export error: " + data.error, true);
        finish();
        return;
      }
      try {
        context.putImageData(
          new ImageData(new Uint8ClampedArray(data.buffer), width, height),
          0, 0
        );
        canvas.toBlob((blob) => {
          if (!blob) {
            status("Could not encode PNG. Try the desktop application.", true);
            finish();
            return;
          }
          const base = (state.filename || "photo")
            .replace(/\.[^.]+$/, "")
            .replace(/[^a-zA-Z0-9._-]/g, "_");
          downloadBlob(blob, base + "_picvidfix.png");
          status("Exported " + width + " × " + height + " PNG locally.");
          toast("Image saved. No file was uploaded.");
          finish();
        }, "image/png");
      } catch (error) {
        status("PNG encoding failed: " + error.message, true);
        finish();
      }
    };
    worker.onerror = () => {
      status("Export worker failed. Try a smaller image or the desktop edition.", true);
      finish();
    };
    status("Applying full-resolution color corrections...");
    worker.postMessage({
      id: 1, width, height, buffer: imageData.data.buffer, settings,
      spatial, mode: "export"
    }, [imageData.data.buffer]);
  } catch (error) {
    status("Export failed: " + error.message, true);
    finish();
  }
}

function installEvents() {
  for (const id of ["openButton", "emptyOpenButton"]) {
    $(id).addEventListener("click", () => elements.fileInput.click());
  }
  for (const id of ["demoButton", "emptyDemoButton"]) {
    $(id).addEventListener("click", loadDemo);
  }
  for (const id of ["resetAllButton", "sidebarResetButton"]) {
    $(id).addEventListener("click", resetAll);
  }
  $("savePresetButton").addEventListener("click", savePreset);
  $("loadPresetButton").addEventListener("click", () => elements.presetInput.click());
  elements.download.addEventListener("click", exportPNG);

  elements.fileInput.addEventListener("change", async () => {
    await loadFile(elements.fileInput.files?.[0]);
    elements.fileInput.value = "";
  });
  elements.presetInput.addEventListener("change", async () => {
    await loadPreset(elements.presetInput.files?.[0]);
    elements.presetInput.value = "";
  });
  elements.compareSlider.addEventListener("input", updateSplit);
  elements.remapEnabled.addEventListener("change", () => {
    state.settings.remap_enabled = elements.remapEnabled.checked;
    schedulePreview();
  });
  elements.remapHex.addEventListener("input", () => {
    state.settings.remap_hex = elements.remapHex.value;
    if (/^#?[0-9a-f]{6}$/i.test(state.settings.remap_hex)) schedulePreview();
  });
  elements.remapHex.addEventListener("blur", () => {
    try {
      state.settings = validatedSettings(state.settings);
      elements.remapHex.value = state.settings.remap_hex;
    } catch (error) {
      status(error.message, true);
    }
  });
  elements.sample.addEventListener("click", () => {
    if (!state.sourcePreview) return;
    setPatchSelection(!state.selectingPatch);
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && state.selectingPatch) setPatchSelection(false);
  });

  // Intercept drops so dropping a video never navigates away from the editor.
  window.addEventListener("dragover", (event) => event.preventDefault());
  window.addEventListener("drop", (event) => {
    event.preventDefault();
    loadFile(event.dataTransfer?.files?.[0]);
  });
}

buildControls();
installEvents();
installPatchSelection();
updateSplit();
syncAllControls();
status("Ready. Pick a photo or try the demo. No uploads are performed.");

// Export the pure engine here too for easy integration testing if needed.
export { correctPixels };
