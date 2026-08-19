"""Rasterize app/static/favicon.svg into Windows .ico and macOS .icns.

Requires `rsvg-convert` (librsvg). The generated binaries are committed so
packaging CI does not need librsvg.
"""

from __future__ import annotations

import argparse
import shutil
import struct
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SVG = ROOT / "app" / "static" / "favicon.svg"
OUT_DIR = Path(__file__).resolve().parent

ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)
ICNS_PNG = (
    ("icp4", 16),
    ("icp5", 32),
    ("icp6", 64),
    ("ic07", 128),
    ("ic08", 256),
    ("ic09", 512),
    ("ic10", 1024),
    ("ic11", 32),
    ("ic12", 64),
    ("ic13", 256),
    ("ic14", 512),
)


def render_png(svg: Path, size: int, dest: Path) -> None:
    rsvg = shutil.which("rsvg-convert")
    if not rsvg:
        raise SystemExit("rsvg-convert not found (install librsvg2-bin)")
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [rsvg, "-w", str(size), "-h", str(size), str(svg), "-o", str(dest)],
        check=True,
    )


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
    parser = argparse.ArgumentParser(description="Render favicon.svg to app.ico and app.icns")
    parser.add_argument("--svg", type=Path, default=SVG)
    parser.add_argument("--output", type=Path, default=OUT_DIR)
    args = parser.parse_args(argv)

    svg = args.svg.resolve()
    if not svg.is_file():
        raise SystemExit(f"missing SVG: {svg}")
    out = args.output
    out.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        png_by_size: dict[int, bytes] = {}
        needed = sorted({1024, *ICO_SIZES, *(size for _, size in ICNS_PNG)})
        for size in needed:
            png_path = tmp / f"{size}.png"
            render_png(svg, size, png_path)
            png_by_size[size] = png_path.read_bytes()

        (out / "app-1024.png").write_bytes(png_by_size[1024])
        write_ico(out / "app.ico", [(size, png_by_size[size]) for size in ICO_SIZES])
        write_icns(
            out / "app.icns",
            [(kind.encode("ascii"), png_by_size[size]) for kind, size in ICNS_PNG],
        )

    print(f"Wrote {out / 'app.ico'}", flush=True)
    print(f"Wrote {out / 'app.icns'}", flush=True)
    print(f"Wrote {out / 'app-1024.png'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
