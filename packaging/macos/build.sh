#!/usr/bin/env bash
# Freeze the program, wrap it in an installer package (a launchd service) and put that in a DMG.
# Run on macOS. Requires: Python 3.12+ (CI uses actions/setup-python).

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

PYTHON="${PYTHON:-python3}"
SKIP_PIP="${DICOMM_SKIP_PIP:-}"

if [[ -z "${DICOMM_DMG_VERSION:-}" ]]; then
  DICOMM_DMG_VERSION="$("$PYTHON" -c "from app import __version__; print(__version__)")"
fi
export DICOMM_DMG_VERSION

if [[ -z "${DICOMM_DMG_ARCH:-}" ]]; then
  DICOMM_DMG_ARCH="$("$PYTHON" -c "import platform; m=platform.machine().lower(); print('arm64' if m in {'arm64','aarch64'} else 'x86_64' if m in {'x86_64','amd64'} else m)")"
fi

if [[ "$SKIP_PIP" != "1" ]]; then
  "$PYTHON" -m pip install --upgrade pip
  "$PYTHON" -m pip install -r requirements.txt pyinstaller
fi

"$PYTHON" -m PyInstaller --noconfirm --clean packaging/macos/dicommunication.spec

ONEDIR="dist/dicommunication"
if [[ ! -x "$ONEDIR/dicommunication" ]]; then
  echo "frozen program missing: $ONEDIR/dicommunication" >&2
  exit 1
fi
"$ONEDIR/dicommunication" --help >/dev/null

"$PYTHON" packaging/macos/make_pkg.py "$ONEDIR" \
  --version "$DICOMM_DMG_VERSION" \
  --arch "$DICOMM_DMG_ARCH" \
  --output dist

"$PYTHON" packaging/macos/make_dmg.py "dist/dicommunication-${DICOMM_DMG_VERSION}-macos-${DICOMM_DMG_ARCH}.pkg" \
  --version "$DICOMM_DMG_VERSION" \
  --arch "$DICOMM_DMG_ARCH" \
  --output dist

echo "Built dist/dicommunication-${DICOMM_DMG_VERSION}-macos-${DICOMM_DMG_ARCH}.dmg"
