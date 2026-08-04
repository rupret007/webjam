#!/usr/bin/env bash
# Install the native release lock on Intel macOS, building cryptography from
# verified source because upstream no longer publishes x86_64 macOS wheels.

set -euo pipefail
umask 077

readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly ROOT="$(cd "$SCRIPT_DIR/../.." && pwd -P)"
readonly TARGET_LOCK="$ROOT/requirements-lock/macos-x64.txt"
readonly BOOTSTRAP_LOCK="$ROOT/requirements-lock/bootstrap.txt"
readonly BUILD_LOCK="$ROOT/requirements-lock/macos-x64-cryptography-build.txt"
readonly PROVENANCE="$SCRIPT_DIR/CRYPTOGRAPHY-X64-BUILD-PROVENANCE.txt"
readonly FILTER_LOCK="$SCRIPT_DIR/prepare-macos-x64-runtime-lock.py"
readonly VERIFY_WHEEL="$SCRIPT_DIR/verify-cryptography-x64-wheel.py"
readonly VERIFY_RUNTIME="$SCRIPT_DIR/verify-cryptography-x64-runtime.sh"
readonly CRYPTOGRAPHY_VERSION="50.0.0"
readonly CRYPTOGRAPHY_SDIST_SHA256="eeac2acb5a20ed25e0ad6d1df9891a520b78b404266b6d11778f25d5d691a6c9"
readonly CRYPTOGRAPHY_SDIST_URL="https://files.pythonhosted.org/packages/de/41/6cbdcf9142d00fe82836fbb51e503e58088575cf7a0fe1dbff6695bf0840/cryptography-50.0.0.tar.gz"
readonly OPENSSL_VERSION="3.5.7"
readonly OPENSSL_ARCHIVE_SHA256="a8c0d28a529ca480f9f36cf5792e2cd21984552a3c8e4aa11a24aa31aeac98e8"
readonly OPENSSL_ARCHIVE_URL="https://github.com/openssl/openssl/releases/download/openssl-$OPENSSL_VERSION/openssl-$OPENSSL_VERSION.tar.gz"
readonly RUST_VERSION="1.88.0"
readonly RUST_TOOLCHAIN="1.88.0-x86_64-apple-darwin"
readonly RUST_MANIFEST_SHA256="431b7c5c0b9a511d8e31d29b378bbc74124e8521f14beb92d3a5a5f7e7e55449"
readonly RUST_MANIFEST_URL="https://static.rust-lang.org/dist/channel-rust-$RUST_VERSION.toml"
readonly EXPECTED_RUSTC="rustc 1.88.0 (6b00bc388 2025-06-23)"
readonly EXPECTED_CARGO="cargo 1.88.0 (873a06493 2025-05-10)"
readonly PYTHON_VERSION="3.11.9"
readonly DEPLOYMENT_TARGET="13.0"

die() {
  printf 'Intel macOS dependency installation failed: %s\n' "$*" >&2
  exit 1
}

[[ $# -eq 0 ]] || die "this helper does not accept arguments"
[[ "$(uname -s)" == Darwin ]] || die "this helper requires macOS"
[[ "$(uname -m)" == x86_64 ]] || die "this helper requires native x86_64 macOS"

python_bin="${WEBJAM_BUILD_PYTHON:-python3}"
python_path="$(command -v "$python_bin")" || die "Python is required"
[[ -x "$python_path" ]] || die "Python is not executable"

for reviewed in \
  "$TARGET_LOCK" "$BOOTSTRAP_LOCK" "$BUILD_LOCK" "$PROVENANCE" \
  "$FILTER_LOCK" "$VERIFY_WHEEL" "$VERIFY_RUNTIME" "$0"; do
  [[ -f "$reviewed" && ! -L "$reviewed" ]] || \
    die "reviewed input is missing or unsafe: $(basename "$reviewed")"
done
for command in \
  awk cp curl file find grep lipo make otool rustup shasum strings sysctl tar \
  xcrun; do
  command -v "$command" >/dev/null 2>&1 || die "$command is required"
done

actual_python="$($python_bin -c 'import platform; print(platform.python_version())')"
[[ "$actual_python" == "$PYTHON_VERSION" ]] || \
  die "Python must be exactly $PYTHON_VERSION (found $actual_python)"

work_parent="${RUNNER_TEMP:-${TMPDIR:-/tmp}}"
[[ -d "$work_parent" && ! -L "$work_parent" ]] || \
  die "build root is missing or unsafe"
work_dir="$(mktemp -d "$work_parent/webjam-cryptography-x64.XXXXXX")"
cleanup() {
  rm -rf -- "$work_dir"
}
trap cleanup EXIT

download_file() {
  local url=$1
  local output=$2
  local expected_sha=$3
  curl --proto '=https' --tlsv1.2 -fsSL --retry 3 \
    --retry-all-errors -o "$output" "$url"
  printf '%s  %s\n' "$expected_sha" "$output" | shasum -a 256 -c -
}

archive_is_safe() {
  ! tar -tzf "$1" | awk '
    /^\// || /(^|\/)\.\.($|\/)/ { unsafe = 1 }
    END { exit unsafe ? 0 : 1 }
  '
}

# Keep PEP 517 tooling out of the application environment. Both pip itself and
# every backend package are installed in this scratch venv from reviewed,
# hash-locked wheels.
build_venv="$work_dir/build-venv"
"$python_bin" -m venv "$build_venv"
build_python="$build_venv/bin/python"
"$build_python" -m pip install --require-hashes --only-binary=:all: \
  -r "$BOOTSTRAP_LOCK"
"$build_python" -m pip install --force-reinstall --no-deps \
  --require-hashes --only-binary=:all: -r "$BUILD_LOCK"
"$build_python" -m pip check
"$build_python" - <<'PY'
from importlib.metadata import version

expected = {
    "cffi": "2.1.0",
    "maturin": "1.14.1",
    "pip": "26.1.2",
    "pycparser": "3.0",
    "setuptools": "81.0.0",
}
for package, wanted in expected.items():
    actual = version(package)
    if actual != wanted:
        raise SystemExit(f"{package} must be exactly {wanted} (found {actual})")
PY

# Use an isolated rustup home. The reviewed channel manifest is hash-pinned,
# and the resulting rustc/cargo commit identities are checked exactly.
export RUSTUP_HOME="$work_dir/rustup"
export CARGO_HOME="$work_dir/cargo-home"
rust_manifest="$work_dir/channel-rust-$RUST_VERSION.toml"
download_file "$RUST_MANIFEST_URL" "$rust_manifest" "$RUST_MANIFEST_SHA256"
grep -Fqx 'version = "1.88.0 (6b00bc388 2025-06-23)"' "$rust_manifest" || \
  die "Rust manifest does not describe the reviewed compiler"
rustup toolchain install "$RUST_TOOLCHAIN" --profile minimal --no-self-update
export RUSTUP_TOOLCHAIN="$RUST_TOOLCHAIN"
export PATH="$CARGO_HOME/bin:$PATH"
[[ "$(rustc --version)" == "$EXPECTED_RUSTC" ]] || \
  die "the active rustc does not match the reviewed toolchain"
[[ "$(cargo --version)" == "$EXPECTED_CARGO" ]] || \
  die "the active cargo does not match the reviewed toolchain"

# The hash-verified cryptography sdist carries the exact Cargo.lock used here.
# Fetch each registry crate under Cargo's locked checksum enforcement, then
# disable Cargo network access before wheel construction begins.
cryptography_archive="$work_dir/cryptography-$CRYPTOGRAPHY_VERSION.tar.gz"
download_file "$CRYPTOGRAPHY_SDIST_URL" "$cryptography_archive" \
  "$CRYPTOGRAPHY_SDIST_SHA256"
archive_is_safe "$cryptography_archive" || \
  die "cryptography archive contains an unsafe path"
tar -xzf "$cryptography_archive" -C "$work_dir"
cryptography_source="$work_dir/cryptography-$CRYPTOGRAPHY_VERSION"
[[ -f "$cryptography_source/Cargo.toml" \
  && -f "$cryptography_source/Cargo.lock" \
  && ! -L "$cryptography_source/Cargo.toml" \
  && ! -L "$cryptography_source/Cargo.lock" ]] || \
  die "cryptography's locked Rust workspace is missing or unsafe"
cargo fetch --locked --manifest-path "$cryptography_source/Cargo.toml"
export CARGO_NET_OFFLINE=true

# Build a private, static OpenSSL prefix from an exact supported LTS source.
# no-module prevents an ephemeral provider dylib or MODULESDIR from entering
# the wheel; no runner/Homebrew OpenSSL state is used.
openssl_archive="$work_dir/openssl-$OPENSSL_VERSION.tar.gz"
download_file "$OPENSSL_ARCHIVE_URL" "$openssl_archive" \
  "$OPENSSL_ARCHIVE_SHA256"
archive_is_safe "$openssl_archive" || die "OpenSSL archive contains an unsafe path"
tar -xzf "$openssl_archive" -C "$work_dir"
openssl_source="$work_dir/openssl-$OPENSSL_VERSION"
openssl_prefix="$work_dir/openssl-prefix"
[[ -f "$openssl_source/VERSION.dat" && ! -L "$openssl_source/VERSION.dat" ]] || \
  die "OpenSSL source identity is missing"
grep -Fqx 'MAJOR=3' "$openssl_source/VERSION.dat" || die "OpenSSL major mismatch"
grep -Fqx 'MINOR=5' "$openssl_source/VERSION.dat" || die "OpenSSL minor mismatch"
grep -Fqx 'PATCH=7' "$openssl_source/VERSION.dat" || die "OpenSSL patch mismatch"

export MACOSX_DEPLOYMENT_TARGET="$DEPLOYMENT_TARGET"
export CC="$(xcrun -f clang)"
sdk_root_candidate="$(xcrun --sdk macosx --show-sdk-path)"
[[ -d "$sdk_root_candidate" ]] || die "the selected macOS SDK is missing"
SDKROOT="$(cd "$sdk_root_candidate" && pwd -P)"
[[ -f "$SDKROOT/usr/include/stdlib.h" ]] || \
  die "the selected macOS SDK lacks standard C headers"
export SDKROOT
export CFLAGS="-isysroot $SDKROOT"
export CPPFLAGS="-isysroot $SDKROOT"
export LDFLAGS="-isysroot $SDKROOT"
jobs="$(sysctl -n hw.logicalcpu)"
[[ "$jobs" =~ ^[1-9][0-9]*$ ]] || die "could not determine build parallelism"
(
  cd "$openssl_source"
  ./Configure darwin64-x86_64-cc \
    no-zlib no-shared no-module no-comp no-apps no-docs no-tests \
    no-sm2-precomp no-atexit enable-ec_nistp_64_gcc_128 \
    "--prefix=$openssl_prefix" "--openssldir=/etc/ssl"
  make -j"$jobs"
  make install_sw
)
grep -Eq '^# *define OPENSSL_VERSION_STR +"3\.5\.7"$' \
  "$openssl_prefix/include/openssl/opensslv.h" || \
  die "the installed OpenSSL headers are not exactly $OPENSSL_VERSION"
[[ -z "$(find "$openssl_prefix" -type f -name '*.dylib' -print -quit)" ]] || \
  die "the private OpenSSL prefix unexpectedly contains a dynamic library"
for library_name in libcrypto.a libssl.a; do
  library="$(find "$openssl_prefix" -maxdepth 3 -type f \
    -name "$library_name" -print -quit)"
  [[ -f "$library" && ! -L "$library" ]] || \
    die "static $library_name was not produced"
  [[ "$(find "$openssl_prefix" -maxdepth 3 -type f \
    -name "$library_name" | wc -l | tr -d ' ')" == 1 ]] || \
    die "static $library_name inventory is ambiguous"
  [[ "$(lipo "$library" -archs)" == x86_64 ]] || \
    die "static $library_name is not exactly x86_64"
done

# Materialize one wheel offline, then validate its archive, METADATA, WHEEL,
# compatibility tag, RECORD, license inventory, and embedded native extension.
export OPENSSL_DIR="$openssl_prefix"
export OPENSSL_STATIC=1
export PIP_DISABLE_PIP_VERSION_CHECK=1
export ARCHFLAGS="-arch x86_64"
wheel_dir="$work_dir/wheel"
verification_dir="$work_dir/wheel-verification"
mkdir "$wheel_dir" "$verification_dir"
PIP_NO_INDEX=1 PIP_CONFIG_FILE=/dev/null \
  "$build_python" -m pip wheel --no-deps --no-index --no-build-isolation \
    --no-cache-dir --wheel-dir "$wheel_dir" "$cryptography_archive"
wheel="$(find "$wheel_dir" -maxdepth 1 -type f \
  -name 'cryptography-50.0.0-*-macosx_*_x86_64.whl' -print -quit)"
[[ -f "$wheel" && ! -L "$wheel" ]] || die "reviewed Intel wheel was not produced"
[[ "$(find "$wheel_dir" -maxdepth 1 -type f | wc -l | tr -d ' ')" == 1 ]] || \
  die "wheel output inventory is ambiguous"
verified_extension="$verification_dir/_rust.abi3.so"
wheel_evidence="$work_dir/cryptography-wheel-evidence.json"
"$python_bin" "$VERIFY_WHEEL" "$wheel" "$verified_extension" \
  > "$wheel_evidence"
"$VERIFY_RUNTIME" "$verification_dir"
wheel_sha="$($python_bin -c \
  'import json,sys; print(json.load(open(sys.argv[1]))["wheel_sha256"])' \
  "$wheel_evidence")"
extension_sha="$($python_bin -c \
  'import json,sys; print(json.load(open(sys.argv[1]))["extension_sha256"])' \
  "$wheel_evidence")"
[[ "$wheel_sha" =~ ^[0-9a-f]{64}$ ]] || die "wheel evidence hash is malformed"
[[ "$extension_sha" =~ ^[0-9a-f]{64}$ ]] || \
  die "extension evidence hash is malformed"

# Download and install every other runtime package under the original hashes.
# The helper removes exactly the independently verified cryptography block and
# preserves every other lock byte. Installation is offline and binary-only.
runtime_lock="$work_dir/macos-x64-without-cryptography.txt"
"$python_bin" "$FILTER_LOCK" "$TARGET_LOCK" "$runtime_lock" \
  > "$work_dir/runtime-lock-evidence.json"
wheelhouse="$work_dir/wheelhouse"
mkdir "$wheelhouse"
cp "$wheel" "$wheelhouse/$(basename "$wheel")"
"$python_bin" -m pip download --dest "$wheelhouse" --no-deps \
  --require-hashes --only-binary=:all: --no-cache-dir -r "$runtime_lock"
[[ -z "$(find "$wheelhouse" -maxdepth 1 -type f ! -name '*.whl' -print -quit)" ]] || \
  die "runtime wheelhouse contains a non-wheel artifact"
install_lock="$work_dir/macos-x64-verified-wheel.txt"
"$python_bin" "$FILTER_LOCK" "$TARGET_LOCK" "$install_lock" \
  --wheel-sha256 "$wheel_sha" \
  > "$work_dir/install-lock-evidence.json"
"$python_bin" -m pip install --force-reinstall --no-index \
  --find-links "$wheelhouse" --no-deps --require-hashes \
  --only-binary=:all: -r "$install_lock"

installed_extension="$($python_bin - <<'PY'
from importlib.metadata import version
from importlib.util import find_spec
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
import cryptography.hazmat.bindings._rust as rust_bindings
from cryptography.hazmat.backends.openssl.backend import backend
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, ed25519
from cryptography.x509.oid import NameOID

if version("cryptography") != "50.0.0":
    raise SystemExit("installed cryptography version mismatch")
if backend.openssl_version_text() != "OpenSSL 3.5.7 9 Jun 2026":
    raise SystemExit("cryptography is not using the reviewed OpenSSL build")
if find_spec("maturin") is not None:
    raise SystemExit("build-only maturin leaked into the application environment")
message = b"WebJam Intel cryptography verification"
signing_key = ed25519.Ed25519PrivateKey.generate()
signing_key.public_key().verify(signing_key.sign(message), message)
certificate_key = ec.generate_private_key(ec.SECP256R1())
name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "WebJam build check")])
now = datetime.now(timezone.utc)
certificate = (
    x509.CertificateBuilder()
    .subject_name(name)
    .issuer_name(name)
    .public_key(certificate_key.public_key())
    .serial_number(x509.random_serial_number())
    .not_valid_before(now - timedelta(minutes=1))
    .not_valid_after(now + timedelta(minutes=5))
    .sign(certificate_key, hashes.SHA256())
)
if certificate.subject != name:
    raise SystemExit("X.509 creation smoke failed")
raw_path = Path(rust_bindings.__file__)
if raw_path.is_symlink():
    raise SystemExit("installed cryptography Rust extension is a symbolic link")
path = raw_path.resolve(strict=True)
if path.suffix != ".so":
    raise SystemExit("installed cryptography Rust extension is malformed")
print(path)
PY
)"
[[ -f "$installed_extension" && ! -L "$installed_extension" ]] || \
  die "installed cryptography extension is missing or unsafe"
[[ "$(shasum -a 256 "$installed_extension" | awk '{print $1}')" == \
  "$extension_sha" ]] || die "installed extension differs from the verified wheel"
"$VERIFY_RUNTIME" "$(dirname "$(dirname "$(dirname "$installed_extension")")")"
"$python_bin" -m pip check

printf '%s\n' \
  "Verified Intel dependency build:" \
  "  cryptography=$CRYPTOGRAPHY_VERSION" \
  "  cryptography_sdist_sha256=$CRYPTOGRAPHY_SDIST_SHA256" \
  "  cryptography_wheel_sha256=$wheel_sha" \
  "  cryptography_extension_sha256=$extension_sha" \
  "  openssl=$OPENSSL_VERSION" \
  "  openssl_source_sha256=$OPENSSL_ARCHIVE_SHA256" \
  "  rust=$RUST_VERSION" \
  "  architecture=x86_64"
