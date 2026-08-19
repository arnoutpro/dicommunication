#!/usr/bin/env bash
# Freeze Dicommunication.app and wrap it in a UDZO DMG. Run on macOS.
# Requires: Python 3.12+ (CI uses actions/setup-python).

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
  "$PYTHON" -m pip install -r requirements.txt -r requirements-desktop.txt pyinstaller
fi

"$PYTHON" -m PyInstaller --noconfirm --clean packaging/macos/dicommunication.spec

APP="dist/Dicommunication.app"
if [[ ! -x "$APP/Contents/MacOS/dicommunication" ]]; then
  echo "frozen launcher missing: $APP/Contents/MacOS/dicommunication" >&2
  exit 1
fi
"$APP/Contents/MacOS/dicommunication" --help >/dev/null

"$PYTHON" packaging/macos/make_dmg.py "$APP" \
  --version "$DICOMM_DMG_VERSION" \
  --arch "$DICOMM_DMG_ARCH" \
  --output dist

echo "Built dist/dicommunication-${DICOMM_DMG_VERSION}-macos-${DICOMM_DMG_ARCH}.dmg"
