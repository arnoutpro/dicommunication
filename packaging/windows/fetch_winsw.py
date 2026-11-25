"""Fetch WinSW, the small service host the Windows service wraps around
dicommunication.exe, and stage it as ``dicommunication-service.exe``.

WinSW is MIT licensed (see WinSW-LICENSE.txt). It is downloaded from NuGet and
the package must match the pinned SHA-256, so a build never packages a binary
nobody has looked at. To move to a newer WinSW, download its package, check it,
and update WINSW_VERSION and WINSW_NUPKG_SHA256 together.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import sys
import urllib.request
import zipfile
from pathlib import Path

WINSW_VERSION = "2.11.0"
WINSW_NUPKG_SHA256 = "b73c6c10ee634a782cb74024a4d802a6214647e44c432a2cc34b360c64f84766"
WINSW_URL = f"https://api.nuget.org/v3-flatcontainer/winsw/{WINSW_VERSION}/winsw.{WINSW_VERSION}.nupkg"
# The .NET Framework 4.6.1 build: present on every supported Windows 10/11 and Server 2016+.
EXE_IN_PACKAGE = "lib/net461-full/WinSW.NET461.exe"
STAGED_NAME = "dicommunication-service.exe"


def verify(data: bytes, expected_sha256: str = WINSW_NUPKG_SHA256) -> None:
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected_sha256.lower():
        raise ValueError(
            f"WinSW package checksum mismatch: expected {expected_sha256}, got {actual}. "
            "Refusing to package it."
        )


def extract_exe(nupkg: bytes) -> bytes:
    with zipfile.ZipFile(io.BytesIO(nupkg)) as archive:
        try:
            return archive.read(EXE_IN_PACKAGE)
        except KeyError:
            raise ValueError(f"{EXE_IN_PACKAGE} not found in the WinSW package.") from None


def download(url: str = WINSW_URL) -> bytes:
    with urllib.request.urlopen(url, timeout=120) as response:  # noqa: S310 - fixed https URL
        return response.read()


def stage(dest_dir: Path, *, nupkg: bytes | None = None, expected_sha256: str = WINSW_NUPKG_SHA256) -> Path:
    """Download (unless ``nupkg`` is given), verify and write the service host."""
    data = nupkg if nupkg is not None else download()
    verify(data, expected_sha256)
    exe = extract_exe(data)
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / STAGED_NAME
    target.write_bytes(exe)
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dest_dir", type=Path, help="folder to stage dicommunication-service.exe in")
    args = parser.parse_args(argv)
    try:
        target = stage(args.dest_dir)
    except (ValueError, OSError) as exc:
        print(f"fetch_winsw: {exc}", file=sys.stderr)
        return 1
    print(f"Staged WinSW {WINSW_VERSION} as {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
