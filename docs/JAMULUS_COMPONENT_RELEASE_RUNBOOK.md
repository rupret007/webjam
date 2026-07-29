# Jamulus component catalog release runbook — WebJam v0.22.2

This runbook renews the small signed catalog that tells WebJam which exact
Jamulus client/server packages are approved. It does not publish WebJam,
redistribute Jamulus packages, approve HEADLESS, or make a desktop release
Latest.

The first public catalog was sequence 1 for exact WebJam 0.22.0. Its stable
lightweight channel tag, `jamulus-components-v1`, is permanently anchored at
commit `bf64c1165486a654d923c4e3cb6ede69e6458320`. Never move or replace that
tag. v0.22.1 authorization came from the immutable, signature-valid sequence 2
catalog targeting exact WebJam 0.22.1. v0.22.2 requires a signature-valid,
unexpired sequence 3 catalog whose payload targets exact WebJam 0.22.2; the
sequence-2 bytes cannot authorize the new desktop version.

## Trust boundary

- The repository and desktop package contain only the Ed25519 public key.
- The matching private key stays in an owner-private regular non-symlink file
  with mode `0600` on a trusted release workstation. Never put it in the
  repository, an artifact, command-line value, environment variable, log,
  support bundle, chat, issue, or GitHub Actions secret.
- The catalog selects only official Jamulus 3.12.3 client/server entries from
  `core/jamulus_compatibility.py`. It contains exactly eight entries and no
  HEADLESS role.
- Every catalog targets one exact WebJam version, expires within 31 days, and
  uses a sequence exactly one greater than the current public catalog.
- `jamulus-components-v1` remains a public non-Latest prerelease with exactly
  one asset: `WebJam-Jamulus-components-v1.json`.
- The desktop draft remains unpublished if catalog generation, public
  redownload, frozen-runtime verification, or UI verification fails.

## Preflight

1. Work from the exact clean annotated `v0.22.2` tag after tag CI finishes the
   four-target matrix and creates the unpublished desktop draft. Verify that
   the draft contains exactly seven packages plus
   `WebJam-v0.22.2-SHA256SUMS.txt`, and verify all seven checksums. Do not run
   **Publish Verified WebJam Release** yet.
2. Reverify immutable identities:

   ```bash
   test "$(git rev-parse v0.22.0)" = \
     663075ec53aab36cc9de5d1b84aaec0b3733290b
   test "$(git cat-file -t v0.22.0)" = tag
   test "$(git rev-parse 'v0.22.0^{commit}')" = \
     bf64c1165486a654d923c4e3cb6ede69e6458320
   test "$(git rev-parse 'jamulus-components-v1^{commit}')" = \
     bf64c1165486a654d923c4e3cb6ede69e6458320
   test "$(git cat-file -t jamulus-components-v1)" = commit
   ```

3. Download the current public catalog into a new directory. For a first
   sequence-3 attempt, verify sequence 2 against its exact WebJam v0.22.1
   identity:

   ```bash
   mkdir -p /tmp/webjam-component-sequence-2
   gh release download jamulus-components-v1 \
     --repo rupret007/webjam \
     --pattern WebJam-Jamulus-components-v1.json \
     --dir /tmp/webjam-component-sequence-2
   .venv/bin/python -m tools.verify_jamulus_component_catalog \
     --webjam-version 0.22.1 \
     --minimum-sequence 2 \
     /tmp/webjam-component-sequence-2/WebJam-Jamulus-components-v1.json
   ```

   Record its public SHA-256, sequence, signer fingerprint, and expiry. Reject
   any sequence other than 2 for the first transition. If a prior attempt
   already published sequence 3 but desktop promotion did not finish, do not
   regenerate, replace, or advance it. Redownload the exact previously
   recorded sequence-3 bytes, require their SHA-256 to match, verify signature,
   exact WebJam 0.22.2, sequence 3, eight-entry inventory, and future expiry,
   then resume the frozen-package and desktop-promotion gates. Any different
   sequence-3 bytes are equivocation and must stop the release.

4. Confirm the private key remains a regular non-symlink `0600` file and its
   public key matches embedded key ID `webjam-component-2026-07`. Do not print
   or copy the private key.
5. Re-run updater, packaging, license, and real-Jamulus 3.12.2/3.12.3 gates.
   Do not renew approval merely because an upstream asset is still named
   “latest.”

## Create and verify sequence 3

Use an absolute output path. The release tool reads the private key from its
file and does not print it:

```bash
mkdir -p /tmp/webjam-component-sequence-3
.venv/bin/python -m tools.create_jamulus_component_catalog \
  --sequence 3 \
  --validity-days 30 \
  --private-key \
    "$HOME/.config/webjam-release/component-catalog-ed25519-private.pem" \
  --output \
    /tmp/webjam-component-sequence-3/WebJam-Jamulus-components-v1.json
.venv/bin/python -m tools.verify_jamulus_component_catalog \
  --webjam-version 0.22.2 \
  --minimum-sequence 3 \
  /tmp/webjam-component-sequence-3/WebJam-Jamulus-components-v1.json
shasum -a 256 \
  /tmp/webjam-component-sequence-3/WebJam-Jamulus-components-v1.json
```

Inspect the public JSON. It must be one canonical signed envelope targeting
exact WebJam 0.22.2, sequence 3, with no more than 30 days of validity, eight
official Jamulus 3.12.3 entries (client/server for Windows x64, Linux x64,
macOS arm64, and macOS x64), no HEADLESS entry, private path, URL credential,
or secret.

## Publish without moving the channel tag or Latest

Upload only the verified higher-sequence asset:

```bash
gh release upload jamulus-components-v1 \
  /tmp/webjam-component-sequence-3/WebJam-Jamulus-components-v1.json \
  --repo rupret007/webjam \
  --clobber
gh release edit jamulus-components-v1 \
  --repo rupret007/webjam \
  --prerelease \
  --latest=false \
  --title "WebJam Jamulus component catalog v1" \
  --notes \
    "Signed, expiring Jamulus compatibility catalog sequence 3 for exact WebJam v0.22.2. This is not a desktop release."
```

Do not run `git tag -f`, `git push --force`, `gh release delete --cleanup-tag`,
or any command that changes `jamulus-components-v1`. The signed sequence,
expiry, exact WebJam identity, and Ed25519 signature—not tag movement—are the
client trust decision.

## Public verification before desktop promotion

1. Download the public asset into a different new directory. Verify its
   GitHub digest and local SHA-256 match the pre-upload file exactly. Keep the
   verifier snapshot and derive the three independent frozen-smoke inputs from
   those public bytes:

   ```bash
   public_directory="$(
     mktemp -d "${TMPDIR:-/tmp}/webjam-component-public.XXXXXX"
   )"
   gh release download jamulus-components-v1 \
     --repo rupret007/webjam \
     --pattern WebJam-Jamulus-components-v1.json \
     --dir "$public_directory"
   public_catalog=\
   "$public_directory/WebJam-Jamulus-components-v1.json"
   catalog_snapshot="$public_directory/verified-catalog.json"
   .venv/bin/python -m tools.verify_jamulus_component_catalog \
     --webjam-version 0.22.2 \
     --minimum-sequence 3 \
     "$public_catalog" > "$catalog_snapshot"
   catalog_envelope_sha256="$(
     shasum -a 256 "$public_catalog" | awk '{print $1}'
   )"
   github_envelope_sha256="$(
     gh api \
       repos/rupret007/webjam/releases/tags/jamulus-components-v1 \
       --jq '.assets[] | select(.name == "WebJam-Jamulus-components-v1.json") | .digest'
   )"
   test "$github_envelope_sha256" = \
     "sha256:$catalog_envelope_sha256"
   catalog_payload_sha256="$(
     .venv/bin/python -c \
       'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["payload_sha256"])' \
       "$catalog_snapshot"
   )"
   signer_fingerprint_sha256="$(
     .venv/bin/python -c \
       'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["signer_fingerprint_sha256"])' \
       "$catalog_snapshot"
   )"
   ```

   Record all three lowercase 64-character SHA-256 values in the private
   release evidence. Do not source them from the frozen desktop result.
2. Inspect the saved verifier snapshot. Confirm exact sequence 3, exact WebJam
   0.22.2, eight client/server entries, no HEADLESS, and a future expiry.
3. Confirm the component release has `prerelease=true`, `draft=false`, exactly
   one asset, and is not returned by `/releases/latest`. Reverify that its tag
   still resolves to the immutable anchor commit.
4. Extract an exact verified v0.22.2 Mac draft package and run:

   ```bash
   .venv/bin/python tests/support/run_frozen_component_catalog_smoke.py \
     --binary /path/to/WebJam.app/Contents/MacOS/WebJam \
     --expected-version 0.22.2 \
     --expected-sequence 3 \
     --expected-target macos-arm64 \
     --expected-jamulus-version 3.12.3 \
     --expected-catalog-envelope-sha256 "$catalog_envelope_sha256" \
     --expected-catalog-payload-sha256 "$catalog_payload_sha256" \
     --expected-signer-fingerprint-sha256 "$signer_fingerprint_sha256"
   ```

   The fixed-URL probe must report packaged Certifi trust ready, CA
   environment overrides ignored, the explicit redirect allowlist, catalog
   sequence 3, exact eight-entry inventory, Jamulus 3.12.3, and the exact
   independently recorded envelope, payload, and signer digests. Use
   `macos-x64` for the Intel package. It accepts no URL, key, or CA-path input.
5. Launch that same package normally. Open
   **More → Jamulus Updates → Check now** and confirm the UI reports Jamulus
   3.12.3 Available or Ready plus the verified sequence, expiry, and signer
   fingerprint. Exercise the platform's explicit approval path only while the
   Jamulus client, server, Reference Track, recording, practice, reconnect, and
   launch lifecycles are idle.
6. Confirm offline, expired, tampered, and missing-trust fixtures keep the
   current managed version or embedded Jamulus 3.12.2 fallback. Export a
   Support Bundle and verify it contains only bounded reason/trust facts—no
   URL, local path, username, token, credential, or raw exception.
7. Run **Publish Verified WebJam Release** for `v0.22.2` only after steps 1–6
   pass. Confirm `/releases/latest` reports v0.22.2 with its exact eight assets
   while `jamulus-components-v1` remains a non-Latest prerelease.
8. Preserve the published v0.22.1 release and every prior immutable tag. If an
   obsolete unpublished draft is intentionally removed, identify it by release
   ID and first prove that no current tag or published asset points to it.

An expired catalog is fail-closed, not an emergency that justifies bypassing
the process. WebJam continues with the last verified managed version or its
embedded Jamulus 3.12.2 fallback until a correctly signed higher sequence is
available.
