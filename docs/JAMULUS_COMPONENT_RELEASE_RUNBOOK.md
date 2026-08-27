# Jamulus component catalog release runbook

> **v0.22.5 published state:** the public v3 component release is sealed and
> immutable with signed sequence 6 for exact WebJam 0.22.5. Its component
> release remains a public non-Latest prerelease and its channel tag never moves.

> **Historical state:** v1 sequence 4 for v0.22.3 and v2 sequence 5 for v0.22.4
> remain immutable. Neither historical tag, release, nor signed asset moved for
> the v0.22.5 transition.

> **v0.23.0 published fallback-only desktop state:** sealed v3 authorizes exact
> WebJam 0.22.5 and cannot authorize 0.23.0. The v0.23 testing-release workflow
> proved that rejection and published only a fallback-capable desktop
> candidate; it does not publish, move, or replace a component channel. A
> managed v0.23 update
> still requires a new fixed versioned channel, the next monotonic sequence,
> future expiry, exact inventory, offline signature, independent redownload,
> and frozen-package verification. Every such component step is **NOT RUN**.
> That source narrowly authorizes the unchanged audited Jamulus 3.12.2 and
> 3.12.3 identities through exact WebJam 0.23.0 only, allowing a known fallback;
> that baked compatibility is not a signed v0.23 managed-update catalog.

> **v0.24.0 historical fallback-only desktop state:** the protected publisher
> proved that sealed v3 cannot authorize v0.24.0 and did not move it. The exact
> v0.24.0 source extends the unchanged,
> CI-exercised 3.12.2/3.12.3 baked identities through exact WebJam 0.24.0 only
> and rejects 0.24.1. Its immutable historical private test release packages
> embedded 3.12.2 after that catalog-rejection proof; managed download still
> requires a new fixed channel, monotonic sequence, offline signature, exact
> inventory, independent redownload, and frozen-package verification. Every
> component publication step for such a new channel remains **NOT RUN**.

> **v0.25.0 published fallback-only desktop state:** the protected publisher
> proved that sealed v3 still authorizes exact WebJam 0.22.5 only and rejects
> 0.25.0 without moving the component channel. The v0.25.0 source registry
> extends the unchanged audited 3.12.2/3.12.3 identities through exact WebJam
> 0.25.0 and rejects 0.25.1 so the embedded fallback remains known, but that is
> not a signed managed-update authorization. Immutable v0.25.0 is historical
> desktop release `371028390`. No v0.25 component tag, release,
> sequence, asset, signature, or PASS evidence exists. A future managed update
> requires a new fixed channel and the complete procedure below.

> **v0.26.0 published fallback-only desktop state:** the protected publisher
> proved that sealed v3 still authorizes exact WebJam 0.22.5 only and rejects
> 0.26.0 without moving the component channel. The v0.26.0 source registry
> recognizes the unchanged audited 3.12.2/3.12.3 identities through exact
> WebJam 0.26.0, but that is not signed managed-update authorization. Immutable
> v0.26.0 is historical desktop release `371442375`. No v0.26 component
> tag, release, sequence, asset, signature, or PASS evidence exists. A future
> managed update requires a new fixed channel and the complete procedure below.

> **v0.27.0 published fallback-only desktop state:** sealed v3 still authorizes
> exact WebJam 0.22.5 only and cannot authorize 0.27.0. Immutable v0.27.0 is
> GitHub **Latest** desktop release `377546932`. Generic publisher run
> `33036413984` failed on that catalog mismatch. No v0.27 component tag,
> release, sequence, asset, signature, or PASS evidence exists.

> **v0.27.1 source-candidate fallback-only state:** sealed v3 still authorizes
> exact WebJam 0.22.5 only and cannot authorize 0.27.1. The source registry
> extends the unchanged audited 3.12.2/3.12.3 identities through exact WebJam
> 0.27.1 and rejects 0.27.2 so the embedded fallback remains known, but that is
> not a signed managed-update authorization. Shared Track play uses this Mac's
> BlackHole route and the bundled headless client, not a catalog pin. No v0.27.1
> component tag, release, sequence, asset, signature, or PASS evidence exists.
> jamulus-components-v1/v2/v3 stay. A future managed update requires a new
> fixed channel and the complete procedure below.

This runbook records the sealed v1/v2 history and current v3 catalog channel. The
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
The sealed public v2 channel is immutable sequence 5 for exact WebJam 0.22.4,
with eight Jamulus 3.12.3 client/server entries and expiry
`2026-09-03T12:00:00Z`. Its sole asset is
`WebJam-Jamulus-components-v1.json`, with envelope SHA-256
`670746cd925fadc62a57e7dfd24a7d67c50a412ab82bf25d1e0295be567294e3` and
signed payload SHA-256
`c5b034dad933a7ffea670cccecaf308947f5ab93f7fedeb0cde0ce8f9e34e83f`.
All three component releases are non-Latest prereleases, and GitHub's
immutable-release policy prevents replacing any asset in place.
The v2 tag is anchored at commit
`fd1d8cbd80e76cdfb257f26894de452f191e15fa`; release ID `365297898` was
published on 2026-08-05 UTC and is immutable.
The public v3 channel is immutable sequence 6 for exact WebJam 0.22.5, with
eight Jamulus 3.12.3 client/server entries and expiry
`2026-09-05T14:13:12Z`. Its lightweight tag is anchored at commit
`b1de2d826afe01d6696677b14c2dd5efafa87b5b`; release ID `366930115` has one
asset, `WebJam-Jamulus-components-v1.json`, asset ID `505491011`, size `17247`,
and GitHub digest
`sha256:7aa43866b701b4ed609e3837ff548eea16126d2b06c3023fbc34254aa9f84a62`.
The independently downloaded public bytes match the private evidence bytes
with envelope SHA-256
`7aa43866b701b4ed609e3837ff548eea16126d2b06c3023fbc34254aa9f84a62` and
signed payload SHA-256
`57eed122607c0859e82c4b7121cd5e4aaba397f4722b18c36189f1660225eb68`.

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
- `jamulus-components-v2` is the sealed v0.22.4 non-Latest prerelease with
  exactly one immutable asset: `WebJam-Jamulus-components-v1.json`.
- `jamulus-components-v3` is the public versioned boundary for exact WebJam
  0.22.5; it must never move or replace v1/v2 and must remain non-Latest.
- The v0.22.5 desktop draft was required to remain unpublished if catalog
  generation, public redownload, frozen-runtime verification, or UI
  verification failed.

## v0.22.5 versioned-channel transition after the sequence-5 seal

Do not attempt to replace the sealed `jamulus-components-v1` asset or move its
tag, and do not replace or move `jamulus-components-v2`. v0.22.5 introduces a
reviewed, versioned v3 boundary: a new fixed catalog URL, new release and tag
identity, explicit desktop compatibility migration, and the complete signature,
expiry, downgrade, privacy, and four-platform frozen-package proof. The new
channel must remain non-Latest and must not alter historical sequence-4 or
sequence-5 bytes or either desktop release.

### Published v3 catalog and desktop release

The reviewed v3 preparation object was
`b1de2d826afe01d6696677b14c2dd5efafa87b5b`. It reports WebJam 0.22.5, selects
the fixed `jamulus-components-v3` URL, requires sequence 6 for promotion, and
descends from both sealed component histories. The intended
`jamulus-components-v3` tag is lightweight and remains pinned to that exact
commit. The release-control workflow pins the same object. A mismatch or any
attempt to move the tag stops the transition.

The tag, signed sequence-6 bytes, one-asset v3 prerelease, and public
redownload were completed on 2026-08-07. The v3 release is a machine-consumed
catalog channel, not an application download. The remaining desktop gates
below subsequently passed on the same immutable source identity.

1. **PASS — source and CI boundary.** The reviewed release-prep history is on
   `master` at `35426b1f14bc9c09c5207d5d7a5dd5cb79351f0c`; green CI run
   `31191519170` covers tests, transport, reference service, Pocket Stage,
   real Jamulus integrations, and all four desktop builds. The prepared anchor
   remains an ancestor and neither the v3 tag nor release was moved.
2. **PASS — lightweight tag.** The tag was verified as object type `commit`
   at the exact anchor and only that new tag was pushed:
   The one-time command below is retained as historical evidence; do not rerun
   it or move the immutable channel tag.

   ```bash
   set -euo pipefail
   v3_anchor=b1de2d826afe01d6696677b14c2dd5efafa87b5b
   git merge-base --is-ancestor "$v3_anchor" origin/master
   test -z "$(git ls-remote --refs origin refs/tags/jamulus-components-v3)"
   git tag jamulus-components-v3 "$v3_anchor"
   test "$(git cat-file -t refs/tags/jamulus-components-v3)" = commit
   test "$(git rev-parse refs/tags/jamulus-components-v3)" = "$v3_anchor"
   git push origin refs/tags/jamulus-components-v3
   ```

3. **PASS — private sequence-6 generation.** On the trusted release
   workstation, the owner-private Ed25519 key was verified as a regular,
   non-symlink file with mode `0600`, matched key ID
   `webjam-component-2026-07`, and generated sequence 6 exactly once. The key
   and its path are not recorded here. The one-time command below is retained
   as historical evidence; do not regenerate sequence 6:

   ```bash
   set -euo pipefail
   v3_anchor=b1de2d826afe01d6696677b14c2dd5efafa87b5b
   key="$HOME/.config/webjam-release/component-catalog-ed25519-private.pem"
   output_directory="$HOME/private-webjam-evidence/catalog-sequence-6-v0225"
   catalog="$output_directory/WebJam-Jamulus-components-v1.json"
   test -f "$key" && test ! -L "$key"
   test "$(stat -f '%Lp' "$key" 2>/dev/null || stat -c '%a' "$key")" = 600
   test "$(git show "$v3_anchor:webjam_qt/__init__.py" | \
     sed -n 's/^__version__ = "\([0-9.]*\)"$/\1/p')" = 0.22.5
   git diff --quiet "$v3_anchor" HEAD -- \
     core/jamulus_compatibility.py tools/create_jamulus_component_catalog.py
   test ! -e "$output_directory"
   mkdir -m 700 "$output_directory"
   .venv/bin/python -m tools.create_jamulus_component_catalog \
     --sequence 6 \
     --validity-days 30 \
     --private-key "$key" \
     --output "$catalog"
   .venv/bin/python -m tools.verify_jamulus_component_catalog \
     --webjam-version 0.22.5 \
     --minimum-sequence 6 \
     "$catalog" > "$output_directory/verification.json"
   jq -e \
     '.sequence == 6 and .webjam_version == "0.22.5" and .component_count == 8' \
     "$output_directory/verification.json" >/dev/null
   shasum -a 256 "$catalog" > "$output_directory/envelope-sha256.txt"
   ```

4. **PASS — private verification.** The retained snapshot required exactly
   eight official Jamulus 3.12.3 client/server entries, no HEADLESS role, a
   30-day expiry, signer fingerprint
   `ea6ba7a52aa37c0d289f5258d34134d11063e5697ce26fd039c2431d3546a687`, and
   no path, meeting, credential, or environment data. Only one sequence-6 byte
   set exists in the private evidence directory.
5. **PASS — public component release.** Exactly one asset was published as a
   non-Latest prerelease on the already-pushed lightweight tag. The original
   publication command is retained as historical evidence; do not rerun it or
   replace the immutable release asset:

   ```bash
   gh release create jamulus-components-v3 "$catalog" \
     --repo rupret007/webjam \
     --verify-tag \
     --prerelease \
     --latest=false \
     --title "WebJam Jamulus component catalog v3" \
     --notes "Signed, expiring Jamulus compatibility catalog sequence 6 for exact WebJam v0.22.5. This is not a desktop release."
   ```

6. **PASS — public redownload and identity binding.** The public asset was
   downloaded into a new private evidence directory through GitHub, then
   independently checked as one immutable release asset with `draft=false`,
   `prerelease=true`, exclusion from `/releases/latest`, the exact lightweight
   anchor, matching GitHub/local SHA-256, signature-valid sequence 6, exact
   WebJam 0.22.5, exact eight-entry inventory, and future expiry. The release
   and asset IDs, digests, catalog hashes, signer fingerprint, and expiry are
   recorded above and in this follow-up commit.
7. **PASS — post-evidence source CI.** WebJam CI run `31206070715` succeeded
   on exact commit `d7d0039759e8334407fe2e6ed9e42edf0d7ef639`, including all
   four desktop builds. The approved read-only verification dispatch used:

   ```bash
   gh workflow run verify-component-candidate.yml \
     --repo rupret007/webjam \
     --ref master \
     -f source_run_id=<successful-master-ci-run-id> \
     -f expected_sha=<exact-40-character-origin-master-sha>
   ```

   The workflow accepts no operator-supplied catalog tag, URL, version,
   sequence, key, or trust path. It requires that run to be a successful push
   CI run for exact current `master`, permits only the four named desktop
   artifacts plus that SHA's Pocket Stage artifact, binds every artifact ID and
   wrapper digest, and verifies immutable `jamulus-components-v3` sequence 6.
   It records the size and SHA-256 of every contained release file. The DMG and
   Windows Setup containers are inventory- and hash-bound but are not launched
   by this gate; only the four portable ZIP packages are live-launched on their
   native Ubuntu, Windows, Apple-silicon Mac, and Intel Mac runners.
8. **PASS — fixed-v3 package verification.** Run `31208008965` passed all four
   native jobs and the final read-only identity-revalidation job on the exact
   post-evidence source identity, freezing `master` without another source commit
   before the annotated desktop tag. If any source commit had followed the
   evidence commit before tagging, this proof would have been discarded and
   rerun.
   A CI run created before `verify-component-candidate.yml` landed cannot pass
   the exact-current-master policy and is not eligible release evidence.
9. **PASS — desktop tag and protected promotion.** Tag CI run `31208271585`
   and protected promotion run `31210531934` passed for the same commit.
   Immutable desktop release ID `366957478` was published on 2026-08-07 UTC as
   GitHub **Latest** at tag `v0.22.5`. The v3 catalog remains a separate
   non-Latest prerelease.

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
