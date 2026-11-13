/**
 * PicVidFix region selection utilities.
 *
 * A region uses original/full-resolution OUTPUT canvas coordinates.
 * Bounds are half-open: [x, x + width) × [y, y + height).
 * Using half-open bounds makes width/height directly useful for image crops,
 * array slicing, experiment ROIs and future object-detection annotations.
 */

export function rectangleFromCorners(first, second, imageWidth, imageHeight) {
  if (!Number.isInteger(imageWidth) || !Number.isInteger(imageHeight)
      || imageWidth < 1 || imageHeight < 1
      || !first || !second
      || ![first.x, first.y, second.x, second.y].every(Number.isInteger)) {
    return null;
  }

  const clamp = (value, max) => Math.max(0, Math.min(max - 1, value));
  const ax = clamp(first.x, imageWidth);
  const ay = clamp(first.y, imageHeight);
  const bx = clamp(second.x, imageWidth);
  const by = clamp(second.y, imageHeight);
  const left = Math.min(ax, bx);
  const top = Math.min(ay, by);

  return {
    x: left,
    y: top,
    width: Math.abs(ax - bx) + 1,
    height: Math.abs(ay - by) + 1
  };
}

export function formatRectangle(box) {
  if (!box) return "No region selected";
  return `X: ${box.x}  Y: ${box.y}  W: ${box.width}  H: ${box.height}`;
}

export function exportRectangle(box, imageWidth, imageHeight, filename) {
  if (!box || !Number.isInteger(imageWidth) || !Number.isInteger(imageHeight)
      || imageWidth < 1 || imageHeight < 1
      || ![box.x, box.y, box.width, box.height].every(Number.isInteger)
      || box.x < 0 || box.y < 0 || box.width < 1 || box.height < 1
      || box.x + box.width > imageWidth
      || box.y + box.height > imageHeight) {
    throw new Error("No valid image region is selected.");
  }
  return {
    schema: "picvidfix-region-v1",
    image: {
      filename: String(filename || "image"),
      width: imageWidth,
      height: imageHeight
    },
    coordinateSystem: "zero-based, output-image pixels; x2/y2 exclusive",
    region: {
      ...box,
      x2: box.x + box.width,
      y2: box.y + box.height
    },
    normalized: {
      x: box.x / imageWidth,
      y: box.y / imageHeight,
      width: box.width / imageWidth,
      height: box.height / imageHeight
    }
  };
}
