# Contributing to WebJam

WebJam is an active research-and-product project. Contributions are welcome
when they preserve the product's musician-first workflow, explicit ownership
boundaries, privacy contracts, and evidence discipline.

## Before changing code

1. Read [Development](DEVELOPMENT.md), [Architecture](ARCHITECTURE.md), and the
   relevant decision record in `docs/adr/`.
2. Check `CHANGELOG.md`'s `Unreleased` section and open issues before starting.
3. Keep the work focused. A UI improvement, a release change, and a new media
   subsystem should not be one unreviewable change.

## Local checks

From the repository root:

```sh
.venv/bin/ruff check webjam_qt/ core/ ui/ services/ api/ tests/
.venv/bin/python -m compileall -q core webjam_qt ui services api tests
.venv/bin/python -m pip check
.venv/bin/python -m pytest -q
```

Run the smallest relevant focused suite first, then the full suite when a
shared controller, package contract, workflow, or privacy boundary changes.
Physical audio, external Webex, iPhone pairing, signing, and notarization are
separate evidence gates; do not mark them PASS from a source test.

## Change rules

- Keep Jamulus, Webex, and the operating system as explicit external owners.
- Keep Studio edits non-destructive and recording identity durable.
- Never log credentials, invitations, meeting links, raw paths, or raw
  exceptions.
- Add or update tests for behavior, accessibility, privacy, and release
  metadata that your change affects.
- Update the canonical audience guide and changelog entry with the code.
- Do not move an existing release tag or mutate immutable release assets.

Use a short imperative commit subject, explain the user-facing outcome, and
include the exact checks run. Pull requests should be small enough to review
from the diff and the evidence, not from a promise that a later cleanup will
make the boundary safe.

## Release changes

Release work is draft-first and checksum-bound. Read
`docs/DESKTOP_RELEASE_RUNBOOK.md` and
`docs/JAMULUS_COMPONENT_RELEASE_RUNBOOK.md` before touching a tag, package,
catalog, or GitHub release. Unsigned/ad-hoc private candidates must never be
described as production-trusted installers.
