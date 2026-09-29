# App icons

Everything here is vector, so the icons are sharp at every size.

| File | What it is |
| --- | --- |
| `arnoutpro-a.svg` | The arnout.pro brand mark (multicolour "A"), the master for everything below. Also served as the app's favicon and top-bar mark (`app/static/favicon.svg`). |
| `icon-macos.svg` | macOS icon: the A on a white rounded tile with a soft shadow and a glow in its own colours, on Apple's 824-in-1024 tile grid |
| `icon-windows.svg` | Windows icon: the same tile filling more of the square, with a clearer edge so it reads on a white taskbar |
| `icon-small.svg` | 16–32 px on both platforms: the tile edge to edge, and the palest bands swapped for the nearest stronger colour so the A stays whole |
| `app.ico`, `app.icns`, `app-1024.png` | The rendered icons (committed, so packaging CI needs no renderer) |

## Where the mark comes from

`arnoutpro-a.svg` is a trace of the 512 px `public/icon-512-transparent.png` from
[`arnoutpro/pacsadministration`](https://github.com/arnoutpro/pacsadministration),
made in September 2026. It keeps the original's 20 flat colours and replaces its
blurred edges with exact straight lines and corners. Rendered back at 512 px it
matches the original to within 0.4 of 255 on average (0.1% of pixels differ
noticeably, all along edges). Each band is drawn twice, first with a wide stroke
underneath, so no seam between two bands can ever show the background.

## Regenerating

After changing `arnoutpro-a.svg` or the tile layout in `compose.py`:

```bash
pip install resvg-py                # or: brew install librsvg (rsvg-convert)
python packaging/icons/compose.py   # icon-macos.svg, icon-windows.svg, icon-small.svg
python packaging/icons/render.py    # app.ico, app.icns, app-1024.png
```
