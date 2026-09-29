"""Rasterize the icon designs into Windows .ico and macOS .icns.

Sources, all vector, next to this file (compose.py writes the last three):
  icon-macos.svg    macOS sizes from 64 px up (Apple's tile grid)
  icon-windows.svg  Windows sizes from 48 px up (fuller tile, clear edge)
  icon-small.svg    16-32 px on both (edge-to-edge tile, palest bands stronger)

Renders with resvg (`pip install resvg-py`, no system library) or, if that is
not installed, with `rsvg-convert` (librsvg). The generated binaries are
committed so packaging CI needs neither.
"""

from __future__ import annotations

import argparse
import io
import shutil
import struct
import subprocess
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent

SMALL_MAX = 32  # at or below this size, use icon-small.svg

ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)
ICNS_PNG = (
    ("icp4", 16),
    ("icp5", 32),
    ("icp6", 64),
    ("ic07", 128),
    ("ic08", 256),
    ("ic09", 512),
    ("ic10", 1024),
    ("ic11", 32),  # 16 pt @2x
    ("ic12", 64),  # 32 pt @2x
    ("ic13", 256),  # 128 pt @2x
    ("ic14", 512),  # 256 pt @2x
)


def render_png(svg: Path, size: int) -> bytes:
    """PNG bytes of `svg` at size x size."""
    try:
        import resvg_py
    except ImportError:
        rsvg = shutil.which("rsvg-convert")
        if not rsvg:
            raise SystemExit("Install resvg-py (pip install resvg-py) or rsvg-convert (librsvg).")
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "icon.png"
            subprocess.run([rsvg, "-w", str(size), "-h", str(size), str(svg), "-o", str(out)], check=True)
            return out.read_bytes()
    return bytes(resvg_py.svg_to_bytes(svg_string=svg.read_text(encoding="utf-8"), width=size, height=size))


def write_ico(dest: Path, images: list[tuple[int, bytes]]) -> None:
    count = len(images)
    offset = 6 + 16 * count
    header = struct.pack("<HHH", 0, 1, count)
    entries = b""
    payload = b""
    for size, data in images:
        width = 0 if size >= 256 else size
        height = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", width, height, 0, 0, 1, 32, len(data), offset)
        payload += data
        offset += len(data)
    dest.write_bytes(header + entries + payload)


def write_icns(dest: Path, images: list[tuple[bytes, bytes]]) -> None:
    body = b""
    for ostype, data in images:
        if len(ostype) != 4:
            raise ValueError(f"ICNS type must be 4 bytes, got {ostype!r}")
        body += ostype + struct.pack(">I", 8 + len(data)) + data
    dest.write_bytes(b"icns" + struct.pack(">I", 8 + len(body)) + body)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render the icon SVGs to app.ico, app.icns and app-1024.png")
    parser.add_argument("--source", type=Path, default=HERE, help="folder with icon-*.svg")
    parser.add_argument("--output", type=Path, default=HERE)
    args = parser.parse_args(argv)

    src = args.source.resolve()
    designs = {name: src / f"icon-{name}.svg" for name in ("macos", "windows", "small")}
    for path in designs.values():
        if not path.is_file():
            raise SystemExit(f"missing {path} (run compose.py first)")
    out = args.output
    out.mkdir(parents=True, exist_ok=True)

    cache: dict[tuple[str, int], bytes] = {}

    def png(platform: str, size: int) -> bytes:
        design = "small" if size <= SMALL_MAX else platform
        key = (design, size)
        if key not in cache:
            cache[key] = render_png(designs[design], size)
        return cache[key]

    (out / "app-1024.png").write_bytes(png("macos", 1024))
    write_ico(out / "app.ico", [(size, png("windows", size)) for size in ICO_SIZES])
    write_icns(out / "app.icns", [(kind.encode("ascii"), png("macos", size)) for kind, size in ICNS_PNG])

    for name in ("app.ico", "app.icns", "app-1024.png"):
        print(f"Wrote {out / name}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
