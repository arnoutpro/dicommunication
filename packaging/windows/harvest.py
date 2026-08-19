"""Emit a WiX fragment that installs every file in a PyInstaller onedir folder."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
from xml.sax.saxutils import escape

WIX_NS = "http://wixtoolset.org/schemas/v4/wxs"


def wix_id(prefix: str, relative: str) -> str:
    digest = hashlib.sha1(relative.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}{digest}"


def harvest(dist_dir: Path) -> str:
    dist_dir = dist_dir.resolve()
    if not dist_dir.is_dir():
        raise FileNotFoundError(f"PyInstaller output not found: {dist_dir}")

    files = sorted(
        path for path in dist_dir.rglob("*") if path.is_file() and path.name != ".DS_Store"
    )
    if not files:
        raise ValueError(f"No files to harvest in {dist_dir}")

    directories: dict[str, str] = {"": "INSTALLFOLDER"}
    dir_xml: list[str] = []
    components: list[str] = []
    refs: list[str] = []

    def directory_id(relative_dir: str) -> str:
        if relative_dir in directories:
            return directories[relative_dir]
        parent = str(Path(relative_dir).parent)
        parent_id = directory_id("" if parent == "." else parent)
        name = Path(relative_dir).name
        ident = wix_id("dir", relative_dir)
        directories[relative_dir] = ident
        dir_xml.append(
            f'    <DirectoryRef Id="{parent_id}">\n'
            f'      <Directory Id="{ident}" Name="{escape(name)}" />\n'
            f"    </DirectoryRef>"
        )
        return ident

    for path in files:
        relative = path.relative_to(dist_dir).as_posix()
        relative_win = relative.replace("/", "\\")
        parent = str(path.relative_to(dist_dir).parent)
        parent_key = "" if parent == "." else parent.replace("\\", "/")
        parent_id = directory_id(parent_key)
        component_id = wix_id("cmp", relative)
        file_id = wix_id("fil", relative)
        components.append(
            f'    <Component Id="{component_id}" Directory="{parent_id}" Guid="*">\n'
            f'      <File Id="{file_id}" Source="{escape(relative_win)}" KeyPath="yes" />\n'
            f"    </Component>"
        )
        refs.append(f'      <ComponentRef Id="{component_id}" />')

    parts = [
        '<?xml version="1.0" encoding="utf-8"?>',
        f'<Wix xmlns="{WIX_NS}">',
        "  <Fragment>",
        *dir_xml,
        *components,
        '    <ComponentGroup Id="AppFiles">',
        *refs,
        "    </ComponentGroup>",
        "  </Fragment>",
        "</Wix>",
        "",
    ]
    return "\n".join(parts)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Harvest a PyInstaller folder into a WiX fragment.")
    parser.add_argument("dist_dir", type=Path, help="PyInstaller onedir output (dist/dicommunication)")
    parser.add_argument("output", type=Path, help="WiX fragment to write")
    args = parser.parse_args(argv)
    args.output.write_text(harvest(args.dist_dir), encoding="utf-8")
    print(f"Wrote {args.output}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
