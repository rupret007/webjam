# Security policy

WebJam handles live-session state, local recordings, meeting links, device
integration, and packaged third-party software. Security reports are welcome.

## Reporting a vulnerability

Please do not disclose a suspected vulnerability in a public issue. Use
GitHub's private vulnerability reporting flow at:

<https://github.com/rupret007/webjam/security/advisories/new>

If private reporting is unavailable for your account, contact the repository
owner through the GitHub profile and request a private channel. Include the
smallest reproducible description, affected commit or release, platform, and
impact. Do not include passwords, meeting links, tokens, private keys, raw
support bundles, or unredacted local paths.

## What to include

- affected version, commit, or exact release asset and SHA-256;
- operating system and architecture;
- safe reproduction steps or a minimal proof;
- expected versus observed behavior;
- whether the issue affects confidentiality, integrity, availability, or
  release/package trust.

## Scope and limitations

Jamulus, meeting services, Python dependencies, Qt, and operating-system trust systems
have their own upstream security channels. WebJam-specific orchestration,
privacy projection, package verification, updater, transport, and lifecycle
issues belong here. Immutable v0.27.0 remains GitHub **Latest** private test
download; v0.27.1 is currently source-only and unpublished. Use only an exact
checksum-verified release asset. The published release and its immutable
predecessors share this test-only trust boundary: Windows is unsigned and
macOS is ad-hoc signed and unnotarized.

Immutable v0.27.0 remains the GitHub **Latest** private test download; the
exact release assets and `WebJam-v0.27.0-SHA256SUMS.txt` are the downloadable
evidence. Publication did not create physical certification: every physical,
hardware, provider, accessibility, signing, notarization, and release-decision
row remains **NOT RUN**. Unpublished v0.27.1 has no tag, package, or release
ID. Immutable v0.26.0 and earlier releases remain historical evidence and must
not be rebuilt or replaced.

The Conversation boundary accepts any meeting provider only through a
hardened public HTTPS DNS-host link; it rejects embedded credentials, custom
ports, local/special-use names, IP literals, percent-encoded hosts, and
known-brand lookalikes. Acceptance authorizes only an external URL handoff,
not provider identity or native-app verification; validation itself performs
no DNS lookup or network fetch. Known allowlisted services
may retain an origin-only redaction in bounded diagnostics; an unknown
provider's URL and hostname are fully redacted. Native publisher proof,
installation guidance, bring-forward, and mute guidance remain Webex-only.
Neither a meeting-link handoff nor native Webex focus creates a recording
source. WebJam never directly or automatically taps a meeting app, browser, or
system output. Local Originals read explicitly selected input devices; because
external routing can feed content into an input, users must not route meeting
or system-output audio into those inputs. Meeting-service recording remains
externally owned. Review & Rehearsal Preview cannot enable shared notes, visual
synchronization, or media timecode.

The v0.26 Record Session readiness surface receives only a bounded, path-free
presentation snapshot: opaque logical-source IDs, safe participant/source
labels, mono/stereo topology, required/optional status, readiness, bounded
meters, storage/Shared Track summaries, and redacted blockers. It does not
receive device identifiers, media paths, plan fingerprints, RPC credentials,
or mutable service objects. Clicking Start is not authority; the controller
revalidates the private take/plan generation, roster, input maps, guest
obligations, device preflight, storage, and Shared Track identity before
arming. Stable logical-source IDs—not display names or ordering—bind new source
topology through transfer, manifests, recovery, and automatic repeated-take
matching. Missing, duplicate, or changed identity fails closed.

Local scratchpads are profile-scoped, atomically written with mode `0600`, and
read only from fixed regular no-follow files up to 1 MiB. They are never shared
and never enter a session synchronization, meeting handoff, public projection,
or media-timecode stream.

## Disclosure

Maintainers will acknowledge receipt through the private channel, assess
severity and affected release identities, and coordinate a fix or mitigation
before public disclosure when practical. No response-time or bounty guarantee
is made.
