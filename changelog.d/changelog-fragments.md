- Changelog entries now live in per-change files under `changelog.d/`, so
  merges no longer conflict on `CHANGELOG.md`. A new `Changelog fragment` CI
  check requires each PR to add a fragment (or a `.skip` with a reason) and
  not edit `CHANGELOG.md`. `python tools/changelog_fragments.py combine`
  folds them into `## [Unreleased]` at release time.
