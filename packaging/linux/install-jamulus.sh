#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PACKAGE="$SCRIPT_DIR/Jamulus/jamulus_3.12.2_ubuntu_amd64.deb"

if [ ! -f "$PACKAGE" ]; then
    printf '%s\n' "The bundled Jamulus package is missing. Re-extract WebJam and try again." >&2
    exit 1
fi
if ! command -v apt >/dev/null 2>&1; then
    printf '%s\n' "This helper requires an Ubuntu/Debian system with apt." >&2
    exit 1
fi

exec sudo apt install "$PACKAGE"
