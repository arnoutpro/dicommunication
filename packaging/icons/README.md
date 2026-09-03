# App icons

Source art is [`app/static/favicon.svg`](../../app/static/favicon.svg): the arnout.pro brand mark (multicolor "A", transparent background), embedded as a base64 PNG so the favicon does not need a webfont. Source: [`arnoutpro/pacsadministration` `public/icon-512-transparent.png`](https://github.com/arnoutpro/pacsadministration/blob/main/public/icon-512-transparent.png).

`render.py` rasterizes that SVG into:

- `app.ico` — Windows EXE and Start menu shortcut
- `app.icns` — macOS Dock / Finder `.app`
- `app-1024.png` — preview / regeneration check

```bash
sudo apt-get install librsvg2-bin   # or brew install librsvg
python packaging/icons/render.py
```

The `.ico` and `.icns` files are committed so packaging CI does not need librsvg. Regenerating is only needed when the favicon changes.
