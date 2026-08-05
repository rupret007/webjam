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

Jamulus, Webex, Python dependencies, Qt, and operating-system trust systems
have their own upstream security channels. WebJam-specific orchestration,
privacy projection, package verification, updater, transport, and lifecycle
issues belong here. The v0.22.4 downloads are private test candidates:
Windows is unsigned and macOS is ad-hoc signed and unnotarized.

## Disclosure

Maintainers will acknowledge receipt through the private channel, assess
severity and affected release identities, and coordinate a fix or mitigation
before public disclosure when practical. No response-time or bounty guarantee
is made.
