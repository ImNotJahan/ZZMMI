#!/bin/sh
# Build macos/dist/ZZMMI.app and macos/dist/ZZMMI.zip.
# Needs a Python with tkinter (python.org builds have it) and: pip install . py2app
set -eu
cd "$(dirname "$0")"
PYTHON="${PYTHON:-python3}"

"$PYTHON" -c "import tkinter, py2app, zzmmi" || {
    echo "error: $PYTHON needs tkinter, py2app and zzmmi installed (pip install . py2app)" >&2
    exit 1
}
rm -rf build dist
"$PYTHON" setup.py py2app
# Ad-hoc signature: Apple Silicon refuses to run unsigned arm64 code.
codesign --force --deep --sign - dist/ZZMMI.app
ditto -c -k --sequesterRsrc --keepParent dist/ZZMMI.app dist/ZZMMI.zip
echo "Built $(pwd)/dist/ZZMMI.app"
