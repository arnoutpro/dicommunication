"""Compose the app icon designs from the vector "A" (arnoutpro-a.svg).

A white rounded tile with a little depth, the A on top. Run this after
changing arnoutpro-a.svg or the layout below, then render.py for the
.ico / .icns files. Writes, next to this file:
  icon-macos.svg   1024 canvas, Apple's 824 px tile, soft drop shadow, colour glow
  icon-windows.svg 1024 canvas, fuller tile, light shadow, hairline border
  icon-small.svg   for 16-32 px: tile edge to edge, palest bands made stronger
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

# At 16-32 px the palest bands disappear into a white tile: use the nearest
# stronger colour from the same palette there, and only there.
SMALL_SWAP = {
    "ffd3ea": "ffa3c7",  # pale pink -> pink
    "ffc8c8": "ffa3c7",  # blush -> pink
    "ebd7fe": "e879f9",  # pale lilac -> orchid
    "eccefc": "e879f9",  # pale lilac -> orchid
    "e6f1fc": "7ff7fc",  # near-white blue -> light cyan
}


def placed_a(cx: float, cy: float, width: float, body: str) -> str:
    """The A scaled to `width` px wide and centred on (cx, cy)."""
    x0, y0, x1, y1 = A_BOX
    s = width / (x1 - x0)
    tx = cx - s * (x0 + x1) / 2
    ty = cy - s * (y0 + y1) / 2
    return f'<g transform="translate({tx:.2f} {ty:.2f}) scale({s:.5f})">{body}</g>'


def icon(*, tile: float, radius: float, a_width: float, shadow: str, a_shadow: str,
         border: str, body: str, gradient=("#ffffff", "#eef1f6"),
         glow: tuple[float, float, float] | None = None) -> str:
    """One icon on a 1024 canvas: rounded white tile, then the A on top."""
    x = (1024 - tile) / 2
    top, bottom = gradient
    glow_filter = glow_layer = ""
    if glow:
        # A soft halo in the A's own colours, as if they light up the tile a little:
        # a blurred, slightly more saturated copy of the A behind it, kept on the tile.
        blur, opacity, saturate = glow
        glow_filter = f"""
    <filter id="a-glow" x="-40%" y="-40%" width="180%" height="180%">
      <feGaussianBlur in="SourceGraphic" stdDeviation="{blur}"/>
      <feColorMatrix type="saturate" values="{saturate}"/>
      <feComponentTransfer><feFuncA type="linear" slope="{opacity}"/></feComponentTransfer>
    </filter>"""
        # Black bands give off no light, so they sit out of the glow (a grey smudge otherwise).
        glow_body = body.replace('fill="#000000"', 'fill="none"').replace('stroke="#000000"', 'stroke="none"')
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


def small_body() -> str:
    body = A_BODY
    for pale, strong in SMALL_SWAP.items():
        body = body.replace(f"#{pale}", f"#{strong}")
    return body


# macOS: Apple's grid, 824 px tile centred in 1024 with room for the shadow.
# The A's colours glow onto the tile; with that, its own shadow can be lighter.
MACOS = icon(
    tile=824, radius=185, a_width=680,
    shadow='<feDropShadow dx="0" dy="14" stdDeviation="16" flood-color="#0a1330" flood-opacity="0.28"/>',
    a_shadow='<feDropShadow dx="0" dy="6" stdDeviation="6" flood-color="#0a1330" flood-opacity="0.14"/>',
    border="rgba(10,19,48,0.10)", body=A_BODY, glow=(34, 0.8, 1.5),
)

# Windows: icons fill more of their square; lighter shadow, clearer edge so a
# white tile still reads on a white taskbar or Start menu.
WINDOWS = icon(
    tile=920, radius=170, a_width=780,
    shadow='<feDropShadow dx="0" dy="8" stdDeviation="9" flood-color="#0a1330" flood-opacity="0.22"/>',
    a_shadow='<feDropShadow dx="0" dy="7" stdDeviation="6" flood-color="#0a1330" flood-opacity="0.22"/>',
    border="rgba(10,19,48,0.18)", body=A_BODY,
)

# 16-32 px: no room for shadows; tile edge to edge with a firm border,
# the A as large as possible, palest bands strengthened.
SMALL = icon(
    tile=1000, radius=180, a_width=900,
    shadow='<feOffset dx="0" dy="0"/>',
    a_shadow='<feOffset dx="0" dy="0"/>',
    border="rgba(10,19,48,0.32)", body=small_body(), gradient=("#ffffff", "#f4f6fa"),
)

for name, svg in (("icon-macos.svg", MACOS), ("icon-windows.svg", WINDOWS), ("icon-small.svg", SMALL)):
    (HERE / name).write_text(svg)
    print("wrote", name, len(svg), "bytes")
