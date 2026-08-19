# App icons

Source art is [`app/static/favicon.svg`](../../app/static/favicon.svg) — the dark rounded square and aurora “A” used as the browser tab icon.

`render.py` rasterizes that SVG into:

- `app.ico` — Windows EXE and Start menu shortcut
- `app.icns` — macOS Dock / Finder `.app`
- `app-1024.png` — preview / regeneration check

```bash
sudo apt-get install librsvg2-bin   # or brew install librsvg
python packaging/icons/render.py
```

The `.ico` and `.icns` files are committed so packaging CI does not need librsvg. Regenerating is only needed when the favicon changes.
