/**
 * Convert a pointer's viewport coordinates to a pixel on the full-resolution
 * output canvas. Both browser previews and exports retain the source image
 * dimensions, even when a geometry correction changes pixel content.
 *
 * Coordinates are zero-based: upper-left is (0, 0).
 * No DOM or image-processing dependencies are needed.
 */
export function pixelAtPointer(clientX, clientY, rect, imageWidth, imageHeight) {
  if (!Number.isFinite(clientX) || !Number.isFinite(clientY)
      || !Number.isInteger(imageWidth) || !Number.isInteger(imageHeight)
      || imageWidth <= 0 || imageHeight <= 0
      || !rect || !Number.isFinite(rect.left)
      || !Number.isFinite(rect.top)
      || !Number.isFinite(rect.width)
      || !Number.isFinite(rect.height)
      || rect.width <= 0 || rect.height <= 0) {
    return null;
  }

  const screenX = clientX - rect.left;
  const screenY = clientY - rect.top;
  if (screenX < 0 || screenY < 0
      || screenX > rect.width || screenY > rect.height) {
    return null;
  }

  // Floor selects the pixel directly beneath the cursor. Clamping also
  // handles the exact right/bottom boundary without exceeding image size.
  return {
    x: Math.min(imageWidth - 1, Math.floor(screenX * imageWidth / rect.width)),
    y: Math.min(imageHeight - 1, Math.floor(screenY * imageHeight / rect.height))
  };
}

export function formatPixelPosition(position) {
  if (position === null) return "X: —  Y: —";
  return `X: ${position.x.toLocaleString("en-US")}  Y: ${position.y.toLocaleString("en-US")}`;
}
