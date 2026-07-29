# Jamulus component catalog release runbook — WebJam v0.22.0

This runbook publishes or renews the small signed catalog that tells WebJam
which exact Jamulus client/server packages are approved. It does not publish
WebJam, redistribute Jamulus packages, approve HEADLESS, or make a desktop
release Latest.

The first live catalog is a required v0.22.0 release gate. Create the desktop
draft and verify its eight-file inventory first, then publish and validate this
separate component prerelease, exercise an exact draft package against it, and
only then promote the desktop draft to GitHub Latest. If any catalog or updater
check fails, leave the desktop release as an unpublished draft.

## Trust boundary

- The repository and desktop package contain only the Ed25519 public key.
- The matching private key stays in an owner-private file on a trusted release
  workstation. Never copy it into the repository, an artifact, a command-line
  value, an environment variable, a log, chat, issue, or GitHub Actions secret.
- The catalog selects only official Jamulus 3.12.3 client/server entries already
  present in `core/jamulus_compatibility.py`. It excludes HEADLESS.
- Every catalog targets exact WebJam `0.22.0`, expires within 31 days, and uses
  a sequence higher than the prior published catalog.
- `jamulus-components-v1` is a separate prerelease and is explicitly not
  GitHub Latest. Its only asset is
  `WebJam-Jamulus-components-v1.json`.
- The component release must be live, publicly downloadable, signature-valid,
  unexpired, and verified from an exact v0.22.0 draft package before the
  desktop release can be promoted to Latest.

## Preflight

1. Work from the exact clean `v0.22.0` tag after tag CI has completed the full
   four-target matrix and created the unpublished desktop draft. Verify that
   draft contains exactly seven packages plus
   `WebJam-v0.22.0-SHA256SUMS.txt`, and verify all seven checksums. Do not run
   **Publish Verified WebJam Release** yet.
2. Confirm the private key is a regular non-symlink file with mode `0600`.
3. For a renewal, download the current public catalog, verify it, and record its
   signed sequence and expiry:

   ```bash
   mkdir -p /tmp/webjam-component-renewal
   gh release download jamulus-components-v1 \
     --repo rupret007/webjam \
     --pattern WebJam-Jamulus-components-v1.json \
     --dir /tmp/webjam-component-renewal
   .venv/bin/python -m tools.verify_jamulus_component_catalog \
     /tmp/webjam-component-renewal/WebJam-Jamulus-components-v1.json
   ```

   For the first publication, the component release and asset do not exist yet;
   do not weaken the verification command to hide any other download error.
   Use prior sequence `0`. For every renewal, choose exactly the prior sequence
   plus one. Do not reuse a sequence with different content.

4. Re-run the updater, packaging, license, and real-Jamulus compatibility gates.
   Do not renew approval merely because an upstream asset is still named
   “latest.”

## Create and verify

Use an absolute output path and the private-key path; neither is printed:

```bash
rm -f /tmp/webjam-component-renewal/WebJam-Jamulus-components-v1.json
.venv/bin/python -m tools.create_jamulus_component_catalog \
  --sequence NEW_SEQUENCE \
  --validity-days 30 \
  --private-key "$HOME/.config/webjam-release/component-catalog-ed25519-private.pem" \
  --output /tmp/webjam-component-renewal/WebJam-Jamulus-components-v1.json
.venv/bin/python -m tools.verify_jamulus_component_catalog \
  --minimum-sequence NEW_SEQUENCE \
  /tmp/webjam-component-renewal/WebJam-Jamulus-components-v1.json
shasum -a 256 \
  /tmp/webjam-component-renewal/WebJam-Jamulus-components-v1.json
```

Inspect the public JSON. It must contain one canonical signed envelope, eight
official entries (client and server for Windows x64, Linux x64, macOS arm64,
and macOS x64), no HEADLESS entry, no private path, and no secret.

## Publish without changing Latest

For the first publication, create a lightweight component tag at the exact
v0.22.0 commit and a prerelease that is explicitly not Latest:

```bash
git tag jamulus-components-v1 v0.22.0
git push origin refs/tags/jamulus-components-v1
gh release create jamulus-components-v1 \
  /tmp/webjam-component-renewal/WebJam-Jamulus-components-v1.json \
  --repo rupret007/webjam \
  --verify-tag \
  --prerelease \
  --latest=false \
  --title "WebJam Jamulus component catalog v1" \
  --notes "Signed, expiring Jamulus compatibility catalog for WebJam v0.22.0. This is not a desktop release."
```

For renewal, the release must remain mutable. Upload the higher-sequence
catalog with clobber only after all verification passes:

```bash
gh release upload jamulus-components-v1 \
  /tmp/webjam-component-renewal/WebJam-Jamulus-components-v1.json \
  --repo rupret007/webjam \
  --clobber
```

Never move the tag. The signed sequence, expiry, and signature—not tag movement
or asset naming—provide the client trust decision.

## Public verification before desktop promotion

1. Download the asset into a new empty directory and verify it again.
2. Confirm its sole release asset is
   `WebJam-Jamulus-components-v1.json`, and independently verify the signature,
   exact WebJam `0.22.0` identity, eight client/server target entries, sequence,
   and unexpired timestamp with the verifier from the `v0.22.0` tag.
3. Confirm the component release is `prerelease=true`, `draft=false`, and not
   the value returned by `/releases/latest`.
4. Launch a clean package from the exact verified v0.22.0 desktop draft and use
   **More → Jamulus Updates → Check now**. Confirm the catalog reports the
   verified sequence, expiry, and fingerprint and Jamulus 3.12.3 becomes
   Available or Ready. Exercise the platform's explicit approval path without
   interrupting an active Jamulus lifecycle.
5. Confirm offline, expired, and tampered fixtures retain the current managed
   version or embedded 3.12.2 fallback.
6. Run **Publish Verified WebJam Release** for `v0.22.0` only after steps 1–5
   pass. Confirm `/releases/latest` reports the v0.22.0 desktop release with its
   exact eight assets while `jamulus-components-v1` remains a non-Latest
   prerelease.
7. Delete only the temporary public catalog directory. Retain no extra copy of
   the private key.

An expired catalog is fail-closed, not an emergency that justifies bypassing
the process. WebJam continues with the last verified managed version or its
embedded 3.12.2 fallback until a correctly signed higher sequence is available.
