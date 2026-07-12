# Scenehound icons

Minimal paw mark — **pink `#F472B6` on ink `#1A1613`**.

## Files
- `favicon.svg` — scalable favicon (tiled, pink-on-ink)
- `favicon.ico` — multi-size .ico (16 / 32 / 48) for browsers & Docker
- `favicon-16.png`, `favicon-32.png`, `favicon-48.png` — raster favicons
- `icon-256.png`, `icon-512.png` — square app icon (GitHub org/repo avatar, Docker Hub avatar)
- `mark.svg`, `mark-512.png` — transparent paw only (pink), for overlays / light backgrounds
- `social-banner-1280x640.png` — GitHub social preview (Settings → Social preview)

## HTML favicon snippet
```html
<link rel="icon" type="image/svg+xml" href="/favicon.svg">
<link rel="icon" type="image/png" sizes="32x32" href="/favicon-32.png">
<link rel="icon" href="/favicon.ico" sizes="any">
<link rel="apple-touch-icon" href="/icon-256.png">
```

## Docker
```dockerfile
LABEL org.opencontainers.image.title="Scenehound"
LABEL org.opencontainers.image.description="Torznab matching proxy between Whisparr and Prowlarr"
```
Use `icon-512.png` as the Docker Hub repository logo.

## Colors
| role   | hex       |
|--------|-----------|
| paw    | `#F472B6` |
| tile   | `#1A1613` |
| paper  | `#F4EEE3` |
