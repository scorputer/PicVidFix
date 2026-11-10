import { correctPixels } from "./color-engine.js";

// Each worker runs the same correction engine for preview and final export.
// Send a transferable RGBA buffer so the main thread does not copy large files
// when handing work to the background thread.
self.onmessage = (event) => {
  const { id, width, height, buffer, settings } = event.data;
  try {
    const result = correctPixels(
      new Uint8ClampedArray(buffer), width, height, settings
    );
    self.postMessage(
      { id, width, height, buffer: result.buffer },
      [result.buffer]
    );
  } catch (error) {
    self.postMessage({ id, error: error?.message || "Unknown processing error" });
  }
};
