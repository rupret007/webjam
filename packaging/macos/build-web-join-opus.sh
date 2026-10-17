#!/usr/bin/env bash
# Build the optional Web Join codec; never run at application startup.
# Usage: build-web-join-opus.sh /absolute/output-directory
# Requires a C compiler, make, curl and shasum. Builds only in a new temp dir.
set -euo pipefail
out=${1:?Usage: build-web-join-opus.sh /absolute/output-directory}
case "$out" in /*) ;; *) echo 'Output directory must be absolute' >&2; exit 2;; esac
version=1.5.2
sha=65c1d2f78b9f2fb20082c38cbe47c951ad5839345876e46941612ee87f9a7ce1
build=$(mktemp -d "${TMPDIR:-/tmp}/webjoin-opus.XXXXXXXX")
trap 'rm -rf "$build"' EXIT
curl --fail --location --proto '=https' --tlsv1.2 \
  "https://downloads.xiph.org/releases/opus/opus-${version}.tar.gz" -o "$build/opus.tar.gz"
actual=$(shasum -a 256 "$build/opus.tar.gz" | awk '{print $1}')
[ "$actual" = "$sha" ] || { echo 'Opus source digest mismatch' >&2; exit 1; }
tar -xzf "$build/opus.tar.gz" -C "$build"
cd "$build/opus-${version}"
./configure --enable-custom-modes --disable-extra-programs --disable-doc --disable-static --prefix="$build/install"
make -j "${WEBJOIN_BUILD_JOBS:-4}"
make check
make install
mkdir -p "$out"
case "$(uname -s)" in
  Darwin)
    cp "$build/install/lib/libopus.0.dylib" "$out/libwebjoin_opus.dylib"
    install_name_tool -id @rpath/libwebjoin_opus.dylib "$out/libwebjoin_opus.dylib"
    ;;
  Linux) cp "$build/install/lib/libopus.so.0" "$out/libwebjoin_opus.so" ;;
  *) echo 'Unsupported codec build platform' >&2; exit 1 ;;
esac
cp COPYING "$out/OPUS-COPYING.txt"
# Keep the exact source beside the artifact for review/reproduction.
cp "$build/opus.tar.gz" "$out/opus-${version}-source.tar.gz"
printf 'opus=%s\nsource_sha256=%s\nconfigure=--enable-custom-modes --disable-extra-programs --disable-doc --disable-static\n' "$version" "$sha" > "$out/OPUS-PROVENANCE.txt"
