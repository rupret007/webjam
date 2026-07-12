# Third-Party Notices

WebJam's own code is MIT-licensed (see `LICENSE`). This file covers
third-party software that WebJam **bundles** (ships inside its own
installers/artifacts) rather than merely depending on at build time.

## Jamulus

WebJam bundles the official, unmodified [Jamulus](https://jamulus.io)
client — source at [jamulussoftware/jamulus](https://github.com/jamulussoftware/jamulus)
— to remove the "go install Jamulus yourself" step for most users.

- **Version bundled:** `3.12.2` (tag [`r3_12_2`](https://github.com/jamulussoftware/jamulus/releases/tag/r3_12_2))
- **License:** GNU General Public License v2 (core), with Jamulus's own
  new contributions moving toward AGPL v3.0. Bundled components (OPUS,
  STK) carry their own permissive licenses. The full license text as
  shipped by the Jamulus project is included verbatim at
  [`licenses/JAMULUS_COPYING.txt`](licenses/JAMULUS_COPYING.txt) and is
  also placed alongside the bundled copy in every WebJam build (macOS:
  `WebJam.app/Contents/Resources/THIRD_PARTY_LICENSES/`; Windows:
  `Jamulus/` next to the bundled installer).
- **How it's bundled, per platform:**
  - **macOS:** the Apple-signed, notarized `Jamulus.app` published in
    Jamulus's own release `.dmg` is extracted at build time, completely
    unmodified, and nested inside `WebJam.app/Contents/Resources/Jamulus.app`.
    WebJam launches it as a separate OS process and talks to it only over
    its public JSON-RPC/UDP interfaces — it is never linked against or
    merged into WebJam's own code.
    The same release's Apple-signed, notarized `JamulusServer.app` is nested
    unmodified beside the client so a designated host needs no separate
    install. WebJam launches each as an independent process.
  - **Windows:** Jamulus only publishes an NSIS *installer* executable
    (no portable binary), so WebJam ships that unmodified installer inside
    its own install directory (`Jamulus/jamulus_3.12.2_win.exe`) and offers
    an "Install Jamulus now" button in the Setup Wizard that simply runs it.
- **Why this doesn't require relicensing WebJam:** Redistributing an
  unmodified third-party binary that is invoked as a separate process
  (never statically or dynamically linked into WebJam's own executable)
  is "mere aggregation" under GPL §2 — it does not bring WebJam's own
  code under the GPL. WebJam communicates with the Jamulus process
  exclusively via `subprocess.Popen` plus its public JSON-RPC/UDP
  protocols, the same way it would talk to a separately-installed copy.
- **Escape hatch:** the bundled copy is only ever used as a *fallback*
  candidate. Users (or admins) can still point WebJam at any other
  Jamulus install via the Setup Wizard's Browse button or the
  `WEBJAM_JAMULUS_CANDIDATES` environment variable — bundling never
  removes that override.
- **Staying current:** because the bundled copy's version is pinned to
  WebJam's own release cadence, a security or bug fix released upstream
  by the Jamulus project won't reach bundled-copy users until the next
  WebJam release. The manual-path override above remains available if
  you need a newer (or different) Jamulus version sooner.

## VB-CABLE (Windows audio routing)

WebJam bundles the VB-CABLE installers (see `VB/readme.txt` for VB-Audio
Software's own license terms) under the terms of that EULA, which
explicitly permits redistribution alongside another application's
installer.

## Webex

WebJam opens Cisco Webex externally for native speech/video. Webex itself is
not bundled. The deprecated Guest Issuer flow is not used or configured by
the current application; legacy source remains only for compatibility review.
