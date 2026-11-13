# PicVidFix Web v0.3.2

A free static photo editor that performs processing locally in the browser.
Deploy to Cloudflare Pages, GitHub Pages, or any static HTTP server. It uses no
server uploads, paid image-processing API, package installation, or cloud jobs.

## Included features

- Image open, drag and drop, and an offline-generated demo image
- Precise numeric inputs and sliders for exposure, tone, color and sharpness
- Split before/after comparison
- Live X/Y pixel coordinates on image hover or touch, scaled to full image resolution
- **Zoom:** Fit, 100%, zoom buttons, slider, Ctrl + wheel; scroll or pan images
- **Bounding box:** draw one persistent rectangle, view X/Y/W/H and export JSON
- White-balance calibration from a neutral patch
- Selective blue replacement
- **Geometry:** straighten/rotate, vertical/horizontal perspective, flip
- **Local correction:** draggable linear foreground/background or radial masks
- Regional exposure and saturation with smooth feathering
- Individual controls, geometry and gradient reset buttons
- JSON presets retaining geometry and gradient handle coordinates
- Original-resolution PNG export through the same processing functions
- Web Workers keep editing responsive and avoid server processing
- Friendly link to desktop version for videos and oversized images

## Use the new correction tools

### Geometry

Open **Geometry**, drag the straighten slider to level a horizon, and use
vertical or horizontal perspective to adjust converging lines. Flip controls
mirror the image. Geometry keeps the original canvas size, so rotated corners
can become transparent. Reset Geometry restores all geometry defaults.

### Regional gradient

Open **Regional gradient**, enable **Apply regional adjustments**, and select
either **Linear** or **Radial**. Drag the **Full** handle over the subject or
foreground to receive all of the correction. Drag the **Fade** handle into the
area that should be preserved. Change regional exposure and saturation.
**Swap direction** interchanges the handles. Untick **Show draggable handles**
for an unobstructed preview. Gradient handles are stored as normalized
coordinates to preserve their positions when exporting at original resolution.

This release supports **one gradient at a time**, not brush masks, segmentation
or multiple combined masks. Geometry and color adjustments are non-destructive
until export. No AI subject-tracking or video editing exists in the browser yet.

## Zoom, pan, and large images

The browser accepts images up to 24 megapixels and input files up to 75 MB
(subject to available device memory). A 5000 × 3500 photo is under the pixel
limit. Use **Fit** to view the whole image, **100%** to inspect source-sized
pixels, or the zoom slider and +/- buttons for intermediate magnification.
The editor adds scrollbars when the image exceeds the viewport; you can also
activate **Pan** or hold Space while dragging. Ctrl + mouse wheel zooms around
the pointer. These operations change *only* the preview viewport and never
downsample or overwrite the original file.

For speed, extremely high zoom levels can display an upscaled preview rather
than reading every full-resolution pixel. Export remains original resolution.

## Select an analysis bounding box

Click **Draw box**, then drag from one image corner to the opposite corner.
The rectangle remains visible while editing, changing zoom, or panning.
Click **Draw box** again to replace it with another rectangle. Use
**Clear box** to remove it. **Export box JSON** downloads the rectangle
coordinates together with the original image dimensions, filename, and
normalized coordinates.

The readout uses **X/Y** for the top-left position and **W/H** for the width
and height.
A second readout shows **TL, TR, BL, BR**, the four included pixel corners,
updated live during drawing. For a rectangle at X=100, Y=200, W=300, H=150,
BR is (399, 349). The corners are inclusive while JSON x2/y2 remain exclusive. Coordinates are zero-based on the *output* image canvas after
geometry correction. JSON uses half-open bounds: `x2 = x + width`,
`y2 = y + height`. A 1-pixel rectangle has width and height 1.
The overlay is **not** burned into the corrected PNG.

The region is an analysis annotation, not an additional exposure mask yet.
Creating a box temporarily gives it pointer priority over gradient handles
and white-balance selection; normal tools become available again when
drawing mode is finished. Your next image starts with no selected box.

## Pixel position readout

Move your mouse or pointer across the image. The small on-image HUD and
footer show **X** (horizontal) and **Y** (vertical) in zero-based coordinates
(upper-left is 0, 0). The position is calculated against the original image's
full pixel dimensions, not the downsampled preview. You can also tap the
image on a touch device.

With geometry enabled, the coordinates refer to the **output image canvas**
rather than the corresponding source-image pixel before warping. The before
and after views remain aligned on that canvas. Leaving the image clears the
hover readout (touch keeps the last tapped position).

## Local preview and export caveats

The original and corrected previews are both geometrically transformed, so
split-view comparison remains aligned. The preview uses reduced-resolution
pixels for speed, while export uses original-resolution pixels, allowing slight
differences in sharpening, feathering and image resampling. Conversion and
color profile behavior may differ from the OpenCV desktop implementation.

Geometry produces transparent border pixels instead of automatically cropping.
Strong perspective settings can introduce stretched or clipped regions. The
browser limits images to 24 megapixels and input files to 75 MB to reduce
out-of-memory crashes. Please preserve your original images.

## Run locally

From the repository root:

```powershell
py -m http.server 8787
```

Open <http://localhost:8787/web/>. Do not use a direct `file://` URL;
module Web Workers typically require an HTTP or HTTPS origin.

## Run automated checks

Requires Node.js 20+; no dependencies or npm install needed:

```powershell
cd web
npm run check
npm test
```

## Publish on Cloudflare Pages with $0 hosting

1. In Cloudflare, create a **Pages** project connected to GitHub.
2. Connect `scorputer/PicVidFix`, branch `main`.
3. Choose no framework, leave the build command empty, set the **root directory
   to the repository root** and the **output directory to `web`**.
4. Deploy to the generated `*.pages.dev` site.

Dashboard wording and free-tier limits can change. The site does not require
Cloudflare Containers or R2.

## Privacy

All local image processing is performed in a browser Worker. Source image
bytes are not transmitted to a processing server by this application. The
application makes no network calls to transmit user images, but the hosting
provider still serves static website assets.

## Future improvements

- Promote an analysis ROI to a reusable exposure, color or tracking mask
- Luminance masks for highlights, shadows, and subject protection
- Multiple editable masks with blend/combination modes
- Edge-aware masking, semantic subject segmentation
- Tone curves, lens distortion correction, inpainting, dehazing
- Browser video preview and desktop processing handoff
