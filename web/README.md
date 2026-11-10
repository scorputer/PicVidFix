# PicVidFix Web v0.1

A free static photo editor running entirely on the user's computer. Host the
files on Cloudflare Pages, GitHub Pages, or any HTTP static server. No
Cloudflare Workers, R2 storage, paid services, build tooling, or server uploads
are used.

## Included features

- JPG/PNG/WebP and other browser-supported image decoding
- Editable numeric fields and sliders for light, color, sharpness and RGB gains
- Selective blue remap with editable hue bounds
- Before/after split preview
- Patch-based white balance using a sampled rectangle
- JSON preset export and import, including basic import of desktop presets
- PNG export at original resolution, using the same JS correction pipeline
- Web Workers keep the UI responsive during preview and export
- Friendly desktop fallback messaging for videos and very large images
- A generated demo image to explore without uploading any files

**Limitations:** The browser prototype currently edits still images only.
Unlike the desktop version, it does not yet offer video export or dynamic
subject-priority auto-exposure. Processing is performed at preview resolution
for interactive display and full resolution for PNG export. This causes possible
scale-dependent differences around sharp edges, Gaussian blue-mask feathering,
and sharpening. Canvas color conversions can differ slightly from OpenCV, and
browser decode/encode pipelines may alter embedded image metadata or profiles.
The UI limits inputs to 24 megapixels and 75 MB to reduce memory crashes.

## Run locally

From the repository root, start a web server:

```powershell
py -m http.server 8787
```

Then open <http://localhost:8787/web/>. Do not open `index.html` directly
with the `file://` protocol because module Web Workers may be blocked.

## Automated tests

Node.js 20+:

```powershell
cd web
npm test
npm run check
```

Tests need no npm install and no third-party dependencies.

## Deploy on Cloudflare Pages for $0

1. Log into Cloudflare and open **Workers & Pages → Create → Pages**.
2. Connect the GitHub repository **scorputer/PicVidFix**.
3. Select the `main` branch.
4. Configure: **framework preset: None**, **build command: blank**,
   **build output directory: `web`**, **root directory: repository root**.
5. Deploy. Cloudflare supplies a `*.pages.dev` address. A custom domain is
   optional.

Cloudflare's dashboard labels and free-tier quotas may change. This setup
only hosts static files; the browser does all image processing.

## Privacy and architecture

JavaScript code performs image corrections in a dedicated Web Worker and
delivers the resulting PNG through a local Blob URL. It does not send files
to an API, server, or storage bucket. GitHub is used only to serve the editor's
static code at deployment time. User-supplied images never leave the browser
through the editor.

For larger videos, use the existing desktop Python app in the repository root.
