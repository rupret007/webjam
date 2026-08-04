#!/usr/bin/env bash
# Verify the source-built cryptography extension in an Intel WebJam bundle.

set -euo pipefail

die() {
  printf 'Intel cryptography runtime verification failed: %s\n' "$*" >&2
  exit 1
}

[[ $# -eq 1 ]] || die "usage: $0 <bundle-root>"
bundle=$1
[[ -d "$bundle" && ! -L "$bundle" ]] || die "bundle root is missing or unsafe"
for command in file find grep lipo otool strings; do
  command -v "$command" >/dev/null 2>&1 || die "$command is required"
done

extension="$(find "$bundle" -type f -name '_rust*.so' -print -quit)"
[[ -n "$extension" && -f "$extension" && ! -L "$extension" ]] || \
  die "cryptography Rust extension is missing or unsafe"
[[ "$(find "$bundle" -type f -name '_rust*.so' | wc -l | tr -d ' ')" == 1 ]] || \
  die "cryptography Rust extension inventory is ambiguous"
[[ "$(lipo "$extension" -archs)" == x86_64 ]] || \
  die "cryptography Rust extension is not exactly x86_64"
file "$extension" | grep -E \
  'Mach-O 64-bit.*(dynamically linked shared library|bundle).*x86_64' \
  >/dev/null || \
  die "file did not identify an x86_64 Mach-O shared library"
linked_libraries="$(otool -L "$extension")" || \
  die "otool linkage inspection failed"
if printf '%s\n' "$linked_libraries" | tail -n +2 \
  | grep -Ei 'lib(crypto|ssl)' >/dev/null; then
  die "cryptography dynamically links libcrypto or libssl"
fi
load_commands="$(otool -l "$extension")" || \
  die "otool load-command inspection failed"
if printf '%s\n' "$load_commands" | awk '
  $1 == "cmd" && $2 == "LC_RPATH" { want = 1; next }
  want && $1 == "path" { print $2; want = 0 }
' | grep -E '^/(Users|private|tmp|opt|usr/local)/' >/dev/null; then
  die "cryptography contains a build-machine runtime path"
fi
strings "$extension" | grep -F 'OpenSSL 3.5.7 9 Jun 2026' >/dev/null || \
  die "cryptography does not contain the reviewed OpenSSL identity"
