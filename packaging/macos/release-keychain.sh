#!/usr/bin/env bash
# Prepare the short-lived Apple signing identity used by a protected release
# job. Secret values are accepted only through the environment; this script
# must never be run with shell tracing enabled.

set -euo pipefail

readonly COMMAND="${1:-}"
readonly PYTHON_BIN="${WEBJAM_PYTHON_BIN:-python3}"
readonly SECURITY_BIN="${WEBJAM_SECURITY_BIN:-/usr/bin/security}"
readonly CODESIGN_BIN="${WEBJAM_CODESIGN_BIN:-/usr/bin/codesign}"
readonly OPENSSL_BIN="${WEBJAM_OPENSSL_BIN:-/usr/bin/openssl}"
readonly XCRUN_BIN="${WEBJAM_XCRUN_BIN:-/usr/bin/xcrun}"
readonly RUNNER_TEMP_DIR="${RUNNER_TEMP:-${TMPDIR:-/tmp}}"
readonly KEYCHAIN_PATH="$RUNNER_TEMP_DIR/webjam-release-signing.keychain-db"
readonly P12_PATH="$RUNNER_TEMP_DIR/webjam-developer-id.p12"
readonly P8_PATH="$RUNNER_TEMP_DIR/webjam-notary-key.p8"
readonly NOTARY_HISTORY_PATH="$RUNNER_TEMP_DIR/webjam-notary-history.json"

required_names=(
  MACOS_DEVELOPER_ID_P12
  MACOS_DEVELOPER_ID_P12_PASSWORD
  APPLE_NOTARY_KEY_P8
  APPLE_NOTARY_KEY_ID
  APPLE_NOTARY_ISSUER_ID
)

usage() {
  printf 'Usage: %s {validate|prepare|cleanup}\n' "$0" >&2
  exit 64
}

require_credentials() {
  local name
  local -a missing=()
  for name in "${required_names[@]}"; do
    if [[ -z "${!name:-}" ]]; then
      missing+=("$name")
    fi
  done
  if (( ${#missing[@]} > 0 )); then
    printf 'macOS release trust credentials are missing: %s\n' \
      "${missing[*]}" >&2
    return 1
  fi
  if [[ -z "${APPLE_DEVELOPER_TEAM_ID:-}" ]]; then
    printf '%s\n' \
      'macOS release trust requires the non-secret APPLE_DEVELOPER_TEAM_ID variable.' >&2
    return 1
  fi
}

validate_credentials() {
  require_credentials
  "$PYTHON_BIN" - <<'PY'
import base64
import binascii
import os
import re


def decode(name: str) -> bytes:
    value = "".join(os.environ[name].split())
    try:
        decoded = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise SystemExit(f"{name} is not valid base64: {exc}") from None
    if not decoded:
        raise SystemExit(f"{name} decodes to an empty value")
    return decoded


p12 = decode("MACOS_DEVELOPER_ID_P12")
if p12[0] != 0x30:
    raise SystemExit("MACOS_DEVELOPER_ID_P12 is not a DER PKCS#12 payload")

p8 = decode("APPLE_NOTARY_KEY_P8")
if not (
    b"-----BEGIN PRIVATE KEY-----" in p8
    and b"-----END PRIVATE KEY-----" in p8
):
    raise SystemExit("APPLE_NOTARY_KEY_P8 is not a PEM private key")

if not re.fullmatch(r"[A-Z0-9]{10}", os.environ["APPLE_NOTARY_KEY_ID"]):
    raise SystemExit("APPLE_NOTARY_KEY_ID must be 10 uppercase letters/digits")
if not re.fullmatch(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
    os.environ["APPLE_NOTARY_ISSUER_ID"],
):
    raise SystemExit("APPLE_NOTARY_ISSUER_ID must be a UUID")
if not re.fullmatch(r"[A-Z0-9]{10}", os.environ["APPLE_DEVELOPER_TEAM_ID"]):
    raise SystemExit("APPLE_DEVELOPER_TEAM_ID must be 10 uppercase letters/digits")
PY
}

decode_credentials() {
  umask 077
  "$PYTHON_BIN" - "$P12_PATH" "$P8_PATH" <<'PY'
import base64
import os
import pathlib
import sys

for env_name, output_name in (
    ("MACOS_DEVELOPER_ID_P12", sys.argv[1]),
    ("APPLE_NOTARY_KEY_P8", sys.argv[2]),
):
    encoded = "".join(os.environ[env_name].split())
    path = pathlib.Path(output_name)
    path.write_bytes(base64.b64decode(encoded, validate=True))
    path.chmod(0o600)
PY
}

cleanup_credentials() {
  # The paths are fixed beneath RUNNER_TEMP, so cleanup is safe even when a
  # previous preparation command failed before exporting environment values.
  "$SECURITY_BIN" delete-keychain "$KEYCHAIN_PATH" >/dev/null 2>&1 || true
  rm -f -- \
    "$KEYCHAIN_PATH" "$P12_PATH" "$P8_PATH" "$NOTARY_HISTORY_PATH"
}

prepare_keychain() {
  validate_credentials
  cleanup_credentials
  trap cleanup_credentials EXIT
  decode_credentials

  local keychain_password identities identity_count identity_hash identity_label
  local team_id
  keychain_password="$($OPENSSL_BIN rand -hex 32)"
  if [[ -z "$keychain_password" ]]; then
    printf 'Could not generate the ephemeral keychain password.\n' >&2
    return 1
  fi
  if [[ -n "${GITHUB_ACTIONS:-}" ]]; then
    printf '::add-mask::%s\n' "$keychain_password"
  fi

  "$SECURITY_BIN" create-keychain -p "$keychain_password" "$KEYCHAIN_PATH"
  "$SECURITY_BIN" set-keychain-settings -lut 21600 "$KEYCHAIN_PATH"
  "$SECURITY_BIN" unlock-keychain -p "$keychain_password" "$KEYCHAIN_PATH"
  "$SECURITY_BIN" import "$P12_PATH" \
    -P "$MACOS_DEVELOPER_ID_P12_PASSWORD" \
    -T "$CODESIGN_BIN" -t cert -f pkcs12 -k "$KEYCHAIN_PATH"
  "$SECURITY_BIN" set-key-partition-list \
    -S apple-tool:,apple:,codesign: \
    -s -k "$keychain_password" "$KEYCHAIN_PATH" >/dev/null

  identities="$($SECURITY_BIN find-identity -v -p codesigning "$KEYCHAIN_PATH")"
  identity_count="$(printf '%s\n' "$identities" | "$PYTHON_BIN" -c '
import re, sys
rows = re.findall(
    r"^\s*\d+\)\s+([0-9A-Fa-f]{40})\s+\"(Developer ID Application:[^\"]+)\"\s*$",
    sys.stdin.read(),
    re.MULTILINE,
)
print(len(rows))
')"
  if [[ "$identity_count" != 1 ]]; then
    printf 'Expected exactly one valid Developer ID Application identity; found %s.\n' \
      "$identity_count" >&2
    return 1
  fi
  identity_hash="$(printf '%s\n' "$identities" | "$PYTHON_BIN" -c '
import re, sys
match = re.search(
    r"^\s*\d+\)\s+([0-9A-Fa-f]{40})\s+\"Developer ID Application:[^\"]+\"\s*$",
    sys.stdin.read(),
    re.MULTILINE,
)
print(match.group(1).upper() if match else "")
')"
  identity_label="$(printf '%s\n' "$identities" | "$PYTHON_BIN" -c '
import re, sys
match = re.search(
    r"^\s*\d+\)\s+[0-9A-Fa-f]{40}\s+\"(Developer ID Application:[^\"]+)\"\s*$",
    sys.stdin.read(),
    re.MULTILINE,
)
print(match.group(1) if match else "")
')"
  team_id="$(printf '%s' "$identity_label" | "$PYTHON_BIN" -c '
import re, sys
match = re.search(r"\(([A-Z0-9]{10})\)\s*$", sys.stdin.read())
print(match.group(1) if match else "")
')"
  if [[ -z "$identity_hash" || -z "$team_id" ]]; then
    printf 'Could not derive the signing identity hash and Team ID.\n' >&2
    return 1
  fi
  if [[ "$team_id" != "$APPLE_DEVELOPER_TEAM_ID" ]]; then
    printf 'Developer ID Team ID does not match APPLE_DEVELOPER_TEAM_ID.\n' >&2
    return 1
  fi

  # This is an authentication preflight, not release evidence. The two real
  # submissions and their logs are retained by release-trust.sh.
  "$XCRUN_BIN" notarytool history \
    --key "$P8_PATH" \
    --key-id "$APPLE_NOTARY_KEY_ID" \
    --issuer "$APPLE_NOTARY_ISSUER_ID" \
    --output-format json > "$NOTARY_HISTORY_PATH"

  if [[ -z "${GITHUB_ENV:-}" ]]; then
    printf 'GITHUB_ENV is required when preparing the release keychain.\n' >&2
    return 1
  fi
  {
    printf 'WEBJAM_MACOS_CODESIGN_IDENTITY=%s\n' "$identity_hash"
    printf 'WEBJAM_MACOS_CODESIGN_TEAM_ID=%s\n' "$team_id"
    printf 'WEBJAM_MACOS_KEYCHAIN=%s\n' "$KEYCHAIN_PATH"
    printf 'WEBJAM_NOTARY_KEY_P8=%s\n' "$P8_PATH"
    printf 'WEBJAM_NOTARY_KEY_ID=%s\n' "$APPLE_NOTARY_KEY_ID"
    printf 'WEBJAM_NOTARY_ISSUER_ID=%s\n' "$APPLE_NOTARY_ISSUER_ID"
  } >> "$GITHUB_ENV"

  trap - EXIT
  printf 'Prepared one Developer ID Application identity for Team %s.\n' "$team_id"
}

case "$COMMAND" in
  validate)
    validate_credentials
    ;;
  prepare)
    prepare_keychain
    ;;
  cleanup)
    cleanup_credentials
    ;;
  *)
    usage
    ;;
esac
