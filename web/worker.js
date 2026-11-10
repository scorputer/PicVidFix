/**
 * PicVidFix photo processing worker.
 *
 * Preview and full-resolution export share the same spatial/color pipeline.
 * Preview also returns a geometrically corrected BEFORE frame for aligned
 * before/after comparison. Export avoids returning that extra buffer.
 */
import { correctImage } from "./spatial-engine.js";

self.onmessage = (event) => {
  const { id, width, height, buffer, settings, spatial, mode } = event.data;
  try {
    const { before, after } = correctImage(
      new Uint8ClampedArray(buffer), width, height, settings, spatial
    );
    if (mode === "preview") {
      self.postMessage(
        { id, width, height, buffer: after.buffer, beforeBuffer: before.buffer },
        [after.buffer, before.buffer]
      );
    } else {
      self.postMessage(
        { id, width, height, buffer: after.buffer },
        [after.buffer]
      );
    }
  } catch (error) {
    self.postMessage({ id, error: error?.message || "Unknown processing error" });
  }
};
