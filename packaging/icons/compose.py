"""Compose the app icon designs from the vector "A" (arnoutpro-a.svg).

A deep navy rounded tile with a little depth, the A on top. Run this after
changing arnoutpro-a.svg or the layout below, then render.py for the
.ico / .icns files. Writes, next to this file:
  icon-macos.svg   1024 canvas, Apple's 824 px tile, soft drop shadow, colour glow
  icon-windows.svg 1024 canvas, fuller tile, light shadow, light edge, colour glow
  icon-small.svg   for 16-32 px: tile edge to edge, no glow
"""

import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
A_SVG = (HERE / "arnoutpro-a.svg").read_text()

# The A's artwork on its 512 canvas: the clip outline and the two band layers.
A_DEFS = re.search(r"<defs>(.*?)</defs>", A_SVG, re.S).group(1).strip()
A_BODY = re.search(r'(<g clip-path="url\(#arnoutpro-a\)">.*</g>)\s*</svg>', A_SVG, re.S).group(1)
A_OUTLINE = re.search(r'<clipPath id="arnoutpro-a"><path fill-rule="evenodd" d="([^"]+)"', A_SVG).group(1)

# The A's ink box on the 512 canvas (from the traced outline).
nums = [float(v) for v in re.findall(r"-?\d+(?:\.\d+)?", A_OUTLINE)]
xs, ys = nums[0::2], nums[1::2]
A_BOX = (min(xs), min(ys), max(xs), max(ys))

# The tile: deep navy, as on arnout.pro in dark mode, so the pastel bands light up.
TILE = ("#222b4d", "#0b1024")

# The glow's palette: each pale band glows as the strong colour it is a tint of,
# so the halo is the logo's pink, orchid and cyan rather than a whitish mist.
GLOW_SWAP = {
    "ffd3ea": "ff6b9a",  # pale pink -> pink
    "ffc8c8": "ff6b9a",  # blush -> pink
    "ffa3c7": "ff6b9a",  # soft pink -> pink
    "fbabfc": "e879f9",  # light orchid -> orchid
    "ebd7fe": "e879f9",  # pale lilac -> orchid
    "eccefc": "e879f9",  # pale lilac -> orchid
    "e6f1fc": "67e8f9",  # near-white blue -> cyan
    "7ff7fc": "5ffbf1",  # light cyan -> aqua
    "78fef7": "5ffbf1",  # light aqua -> aqua
}


def placed_a(cx: float, cy: float, width: float, body: str) -> str:
    """The A scaled to `width` px wide and centred on (cx, cy)."""
    x0, y0, x1, y1 = A_BOX
    s = width / (x1 - x0)
    tx = cx - s * (x0 + x1) / 2
    ty = cy - s * (y0 + y1) / 2
    return f'<g transform="translate({tx:.2f} {ty:.2f}) scale({s:.5f})">{body}</g>'


def icon(*, tile: float, radius: float, a_width: float, shadow: str, a_shadow: str,
         border: str, body: str, gradient=TILE,
         glow: tuple[float, float, float] | None = None) -> str:
    """One icon on a 1024 canvas: rounded navy tile, then the A on top."""
    x = (1024 - tile) / 2
    top, bottom = gradient
    glow_filter = glow_layer = ""
    if glow:
        # A halo in the A's own colours, as if they light up the tile: a blurred,
        # more saturated copy of the A in its strong colours behind it, kept on the tile.
        blur, opacity, saturate = glow
        glow_filter = f"""
    <filter id="a-glow" x="-40%" y="-40%" width="180%" height="180%">
      <feGaussianBlur in="SourceGraphic" stdDeviation="{blur}"/>
      <feColorMatrix type="saturate" values="{saturate}"/>
      <feComponentTransfer><feFuncA type="linear" slope="{opacity}"/></feComponentTransfer>
    </filter>"""
        # Black bands give off no light, so they sit out of the glow (a grey smudge otherwise).
        glow_body = body.replace('fill="#000000"', 'fill="none"').replace('stroke="#000000"', 'stroke="none"')
        for pale, strong in GLOW_SWAP.items():
            glow_body = glow_body.replace(f"#{pale}", f"#{strong}")
        glow_layer = f'  <g clip-path="url(#tile-clip)"><g filter="url(#a-glow)">{placed_a(512, 512, a_width, glow_body)}</g></g>\n'
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024">
  <title>Dicommunication</title>
  <defs>
    <linearGradient id="tile" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="{top}"/>
      <stop offset="1" stop-color="{bottom}"/>
    </linearGradient>
    <filter id="tile-shadow" x="-20%" y="-20%" width="140%" height="150%">{shadow}</filter>
    <filter id="a-shadow" x="-10%" y="-10%" width="120%" height="130%">{a_shadow}</filter>
    <clipPath id="tile-clip"><rect x="{x}" y="{x}" width="{tile}" height="{tile}" rx="{radius}"/></clipPath>{glow_filter}
    {A_DEFS}
  </defs>
  <rect x="{x}" y="{x}" width="{tile}" height="{tile}" rx="{radius}" fill="url(#tile)" filter="url(#tile-shadow)"/>
  <rect x="{x + 1}" y="{x + 1}" width="{tile - 2}" height="{tile - 2}" rx="{radius - 1}" fill="none" stroke="{border}" stroke-width="2"/>
{glow_layer}  <g filter="url(#a-shadow)">{placed_a(512, 512, a_width, body)}</g>
</svg>
"""


# macOS: Apple's grid, 824 px tile centred in 1024 with room for the shadow.
# The A's colours glow onto the tile.
MACOS = icon(
    tile=824, radius=185, a_width=680,
    shadow='<feDropShadow dx="0" dy="14" stdDeviation="16" flood-color="#0a1330" flood-opacity="0.28"/>',
    a_shadow='<feDropShadow dx="0" dy="6" stdDeviation="7" flood-color="#000000" flood-opacity="0.35"/>',
    border="rgba(255,255,255,0.10)", body=A_BODY, glow=(58, 1.5, 3.0),
)

# Windows (48 px and up): icons fill more of their square; lighter shadow, a
# clearer light edge so the tile reads on a dark taskbar. Same glow, scaled.
WINDOWS = icon(
    tile=920, radius=170, a_width=780,
    shadow='<feDropShadow dx="0" dy="8" stdDeviation="9" flood-color="#0a1330" flood-opacity="0.22"/>',
    a_shadow='<feDropShadow dx="0" dy="6" stdDeviation="7" flood-color="#000000" flood-opacity="0.35"/>',
    border="rgba(255,255,255,0.14)", body=A_BODY, glow=(66, 1.5, 3.0),
)

# 16-32 px: no room for shadows or glow; tile edge to edge with a clear
# edge, the A as large as possible.
SMALL = icon(
    tile=1000, radius=180, a_width=900,
    shadow='<feOffset dx="0" dy="0"/>',
    a_shadow='<feOffset dx="0" dy="0"/>',
    border="rgba(255,255,255,0.22)", body=A_BODY,
)

for name, svg in (("icon-macos.svg", MACOS), ("icon-windows.svg", WINDOWS), ("icon-small.svg", SMALL)):
    (HERE / name).write_text(svg)
    print("wrote", name, len(svg), "bytes")
