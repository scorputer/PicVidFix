/**
 * Display-only zoom calculations for PicVidFix Web.
 *
 * Pixels are measured in the original image's output canvas dimensions.
 * These helpers deliberately do not mutate image settings or image data.
 */

export const MIN_ZOOM = 0.05;
export const MAX_ZOOM = 4;

export function fitZoom(imageWidth, imageHeight, viewportWidth, viewportHeight) {
  const dimensions = [imageWidth, imageHeight, viewportWidth, viewportHeight];
  if (dimensions.some((n) => !Number.isFinite(n) || n <= 0)) return null;
  return Math.min(1, viewportWidth / imageWidth, viewportHeight / imageHeight);
}

export function clampZoom(value) {
  if (!Number.isFinite(value)) return null;
  return Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, value));
}

export function zoomDimensions(imageWidth, imageHeight, scale) {
  if (![imageWidth, imageHeight, scale].every(
    (n) => Number.isFinite(n) && n > 0
  )) return null;
  return {
    width: Math.max(1, Math.round(imageWidth * scale)),
    height: Math.max(1, Math.round(imageHeight * scale))
  };
}

/**
 * The CSS image rectangle is in viewport/client coordinates and accounts for
 * the scroll offset. Return the additional scroll distance required to retain
 * the image point under the same cursor position after zooming.
 */
export function anchoredScrollDelta(oldRect, newRect, clientX, clientY) {
  for (const rect of [oldRect, newRect]) {
    if (!rect || !Number.isFinite(rect.left) || !Number.isFinite(rect.top)
        || !Number.isFinite(rect.width) || !Number.isFinite(rect.height)
        || rect.width <= 0 || rect.height <= 0) return null;
  }
  if (!Number.isFinite(clientX) || !Number.isFinite(clientY)) return null;
  const imageX = (clientX - oldRect.left) / oldRect.width;
  const imageY = (clientY - oldRect.top) / oldRect.height;
  return {
    x: newRect.left + imageX * newRect.width - clientX,
    y: newRect.top + imageY * newRect.height - clientY
  };
}
