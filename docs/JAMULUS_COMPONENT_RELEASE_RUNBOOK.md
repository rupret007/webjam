# Jamulus component catalog release runbook

> **v0.22.4 published state:** the public component release is sealed and immutable with signed sequence 5 for exact WebJam 0.22.4. The desktop release is also immutable and GitHub Latest. The component release remains a public non-Latest prerelease and its stable channel tag was not moved.

This runbook records the sealed v1 history and current v2 catalog channel. The
catalog tells WebJam which exact
Jamulus client/server packages are approved. It does not publish WebJam,
redistribute Jamulus packages, approve HEADLESS, or make a desktop release
Latest.

## Current public state

The first public catalog was sequence 1 for exact WebJam 0.22.0. Its stable
lightweight channel tag, `jamulus-components-v1`, is permanently anchored at
commit `bf64c1165486a654d923c4e3cb6ede69e6458320`. Never move or replace that
tag. v0.22.1 authorization came from signature-valid sequence 2. Sequence 3,
independently reverified on 2026-07-29, authorized exact WebJam 0.22.2 through
`2026-08-28T15:03:21Z`.

The sealed v1 channel remains immutable sequence 4 for exact WebJam 0.22.3.
The current public v2 channel is immutable sequence 5 for exact WebJam 0.22.4,
with eight Jamulus 3.12.3 client/server entries and expiry
`2026-09-03T12:00:00Z`. Its sole asset is
`WebJam-Jamulus-components-v1.json`, with envelope SHA-256
`RECORD_AFTER_PUBLIC_UPLOAD` and signed payload SHA-256
`c5b034dad933a7ff670cccecaf308947f5ab93f7fedeb0cde0ce8f9e34e83f`.
Both component releases are non-Latest prereleases, and GitHub's
immutable-release policy prevents replacing either asset in place.

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
- `jamulus-components-v1` remains the sealed v0.22.3 channel.
- `jamulus-components-v2` is the current public non-Latest prerelease with
  exactly one immutable asset: `WebJam-Jamulus-components-v1.json`.
- A future desktop draft remains unpublished if catalog generation, public
  redownload, frozen-runtime verification, or UI verification fails.

## Future renewal after the sequence-5 seal

Do not attempt to replace the sealed `jamulus-components-v1` asset or move its
tag. A future desktop release that needs a renewed catalog must introduce a
reviewed, versioned channel boundary: a new fixed catalog URL, new release and
tag identity, explicit desktop compatibility migration, and the complete
signature, expiry, downgrade, privacy, and four-platform frozen-package proof.
The new channel must remain non-Latest and must not alter historical sequence-4
or sequence-5 bytes or either desktop release.

## Historical mutable-channel renewal procedure: sequence N to N+1

This procedure records how the catalog was renewed before GitHub sealed the
component release. It is no longer executable against `jamulus-components-v1`.
Future renewal must use the versioned-channel boundary above; the exact
v0.22.2 and v0.22.3 commands remain historical evidence, not templates to edit
in place.

1. Choose the exact desktop version to authorize and work from its clean,
   immutable tag in an isolated checkout. Confirm the checked-out source
   reports that exact version. Do not generate from a moving branch, dirty
   tree, or a tag whose desktop packages are not the packages being tested.
2. Create a new private evidence directory and download the current public
   `WebJam-Jamulus-components-v1.json` into it. Record the component release ID,
   asset ID, GitHub `digest`, local SHA-256, signer fingerprint, exact target
   WebJam version, sequence **N**, issue/expiry times, and verified snapshot.
   Require `draft=false`, `prerelease=true`, exactly one asset, and exclusion
   from `/releases/latest`. Verify the asset signature and exact inventory with
   the checked-in verifier before doing anything with the private key.
3. Set **N+1** to exactly one greater than the verified public sequence. If
   public sequence N+1 already exists, do not regenerate, replace, or advance
   it. Redownload those bytes, require the previously recorded SHA-256 and
   signature, verify their exact WebJam target and future expiry, and resume
   package testing with those same bytes. Different bytes at the same sequence
   are equivocation and stop the release.
4. Re-run the compatibility registry, updater, TLS, license, packaging, and
   real-Jamulus gates for the exact target. Confirm the catalog will contain
   exactly eight approved Jamulus 3.12.3 client/server entries—Windows x64,
   Linux x64, macOS arm64, and macOS x64—with no HEADLESS role.
5. On the trusted release workstation, confirm the Ed25519 private key is an
   owner-private regular non-symlink file with mode `0600` and matches embedded
   key ID `webjam-component-2026-07`. Generate once into a new directory and
   non-existing output file:

   ```bash
   : "${TARGET_WEBJAM_VERSION:?set the exact target, for example 0.22.3}"
   : "${CURRENT_SEQUENCE:?set verified public sequence N}"
   case "$CURRENT_SEQUENCE" in
     *[!0-9]*|'') exit 2 ;;
   esac
   test "$CURRENT_SEQUENCE" -ge 1
   next_sequence=$((CURRENT_SEQUENCE + 1))
   output_directory="$HOME/private-webjam-evidence/catalog-sequence-$next_sequence"
   test "$(
     .venv/bin/python -c \
       'from webjam_qt import __version__; print(__version__)'
   )" = "$TARGET_WEBJAM_VERSION"
   test ! -e "$output_directory/WebJam-Jamulus-components-v1.json"
   mkdir -p "$output_directory"
   .venv/bin/python -m tools.create_jamulus_component_catalog \
     --sequence "$next_sequence" \
     --validity-days 30 \
     --private-key \
       "$HOME/.config/webjam-release/component-catalog-ed25519-private.pem" \
     --output \
       "$output_directory/WebJam-Jamulus-components-v1.json"
   .venv/bin/python -m tools.verify_jamulus_component_catalog \
     --webjam-version "$TARGET_WEBJAM_VERSION" \
     --minimum-sequence "$next_sequence" \
     "$output_directory/WebJam-Jamulus-components-v1.json"
   ```

   Inspect the verified snapshot and require its sequence to equal N+1, not
   merely meet the minimum. Require one through 30 days of validity and record
   the new envelope, payload, and signer SHA-256 values without exposing the
   private key.
6. Preserve the verified sequence-N evidence before replacing the stable
   release's one asset. Upload only the new verified N+1 bytes with
   the commands below; `--clobber` is permitted only for this verified
   higher-sequence replacement. Never move the stable tag, create another asset
   name, change Latest, or upload private-key material.

   ```bash
   catalog_file="$output_directory/WebJam-Jamulus-components-v1.json"
   gh release upload jamulus-components-v1 "$catalog_file" \
     --repo rupret007/webjam \
     --clobber
   gh release edit jamulus-components-v1 \
     --repo rupret007/webjam \
     --prerelease \
     --latest=false \
     --title "WebJam Jamulus component catalog v1" \
     --notes \
       "Signed, expiring Jamulus compatibility catalog sequence $next_sequence for exact WebJam v$TARGET_WEBJAM_VERSION. This is not a desktop release."
   ```
7. Download the public asset into a second new directory. Require its GitHub
   digest and local SHA-256 to equal the pre-upload N+1 file exactly, then
   independently reverify its signature, exact sequence, target, expiry, signer,
   and eight-entry inventory. Recheck the one-asset/prerelease/non-Latest
   release contract and immutable channel-tag commit. If upload or verification
   is uncertain, stop; never roll the public asset back to N or create different
   N+1 bytes.
8. Derive the frozen-smoke envelope, payload, and signer digests only from that
   independently downloaded public file. Run the fixed-URL frozen updater probe
   against every exact target desktop package and exercise the packaged update
   UI plus offline, expired, tampered, and missing-trust behavior. A renewal for
   an already published desktop does not change its immutable release assets.
   A new desktop draft remains unpublished until all catalog and package gates
   pass.
9. Retain the old and new public bytes, API metadata, verifier snapshots,
   hashes, frozen-probe results, and UI evidence. Record no private key, local
   private-key path, credential, meeting URL, or raw private environment data.

An expired catalog is a normal fail-closed state. It is safer for WebJam to keep
the last verified managed component or embedded 3.12.2 fallback than to reuse a
sequence, publish unsigned metadata, or bypass exact target verification.

## v0.22.3 sequence-4 publication record

Sequence 3 remains the signed evidence that authorized v0.22.2. It cannot
authorize v0.22.3 and must not be regenerated, re-signed, or replaced by
different sequence-3 bytes. The completed v0.22.3 transition used the
historical N→N+1 procedure above with exact public N=3 and exact new N+1=4.

1. Let the exact annotated `v0.22.3` tag complete its four-target CI build and
   create the unpublished desktop draft. Confirm the draft contains exactly
   seven packages plus `WebJam-v0.22.3-SHA256SUMS.txt`, and verify all seven
   checksums. Do not run **Publish Verified WebJam Release** yet.
2. Independently redownload the current public component asset. Require its
   recorded GitHub digest, signature-valid sequence 3, exact WebJam 0.22.2
   target, eight Jamulus 3.12.3 client/server entries, no HEADLESS entry, and a
   future expiry. Confirm `jamulus-components-v1` remains a one-asset,
   non-Latest prerelease and its stable tag remains anchored at
   `bf64c1165486a654d923c4e3cb6ede69e6458320`.
3. From an isolated clean checkout of the exact `v0.22.3` tag, use the
   owner-private Ed25519 key and the maintained procedure to create sequence 4
   once with at most 30 days of validity. Require exact payload
   `webjam_version` 0.22.3, exact sequence 4, the eight approved Jamulus 3.12.3
   client/server entries, and no HEADLESS role. Record only the envelope,
   payload, and signer SHA-256 values and bounded verification snapshot; never
   record the key or its private path.
4. Replace only the stable component release's one asset with those verified
   higher-sequence bytes, leaving its tag, prerelease status, non-Latest state,
   title, and one-asset inventory intact. Set the public notes to
   `Signed, expiring Jamulus compatibility catalog sequence 4 for exact WebJam v0.22.3. This is not a desktop release.`
5. Redownload the public sequence-4 asset into a second new directory. Require
   its GitHub digest and local SHA-256 to equal the generated bytes exactly,
   then independently verify signature, exact sequence, exact WebJam version,
   expiry, signer, and inventory. Different sequence-4 bytes are equivocation;
   do not regenerate, replace, advance, or roll back the sequence.
6. Bind the three independently derived public digests into the fixed-URL
   frozen catalog smoke for every exact v0.22.3 target. Verify packaged Certifi
   trust, ignored CA overrides, exact redirects, target and architecture,
   Jamulus 3.12.3, offline/expired/tampered/missing-trust behavior, updater UI,
   and privacy-safe diagnostics. Do not modify the release tag or `master`
   between tag creation and desktop promotion.
7. Only after those package and public-catalog gates pass may the verified
   publisher promote the exact v0.22.3 draft. Confirm `/releases/latest`
   reports v0.22.3 while the component release remains a non-Latest
   prerelease. Physical audio, hardware, Webex, Pocket Stage, SmartScreen,
   Gatekeeper, signing, and notarization evidence remains **NOT RUN** unless
   separately recorded against an exact v0.22.3 asset and checksum.

## Historical v0.22.2 sequence-3 publication record

The remaining steps record how sequence 3 was created and verified before
v0.22.2 became GitHub Latest. Preserve their exact identities for audit
history; future renewal must use the new versioned-channel boundary above.

### Preflight

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

### Create and verify sequence 3

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

### Publish without moving the channel tag or Latest

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

### Public verification before desktop promotion

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
