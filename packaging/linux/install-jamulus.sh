#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PACKAGE="$SCRIPT_DIR/Jamulus/jamulus_3.12.2_ubuntu_amd64.deb"
EXPECTED_SHA256=029f8858f21a5fb36da5144046473575caa2a26f2c7d8db162953b89d8c8ccc9

if [ ! -f "$PACKAGE" ]; then
    printf '%s\n' "The bundled Jamulus package is missing. Re-extract WebJam and try again." >&2
    exit 1
fi
if ! command -v apt >/dev/null 2>&1; then
    printf '%s\n' "This helper requires an Ubuntu/Debian system with apt." >&2
    exit 1
fi
if ! command -v sha256sum >/dev/null 2>&1; then
    printf '%s\n' "This helper requires sha256sum to verify the bundled Jamulus package." >&2
    exit 1
fi
if ! printf '%s  %s\n' "$EXPECTED_SHA256" "$PACKAGE" \
    | sha256sum --check --status; then
    printf '%s\n' "The bundled Jamulus package failed its SHA-256 check. Re-extract WebJam and try again." >&2
    exit 1
fi

exec sudo apt install "$PACKAGE"
