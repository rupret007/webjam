# Changelog fragments

Every pull request adds its CHANGELOG entry as its own file here instead of
editing `CHANGELOG.md`. Separate files never conflict, so one merge no longer
breaks every other open PR.

- **Entry:** add `changelog.d/<short-topic>.md` with the Markdown bullet(s)
  exactly as they should read under `## [Unreleased]`, e.g.

  ```markdown
  - Help search shows an Art example when the Art profile is active.
  ```

- **No user-visible change** (CI-only, test-only, internal refactor): add
  `changelog.d/<short-topic>.skip` containing one line saying why. An empty
  `.skip` file is rejected.
- Do not edit the `## [Unreleased]` section of `CHANGELOG.md` in a PR. CI
  (`Changelog fragment`) fails if a PR edits `CHANGELOG.md`, or adds neither a
  non-empty fragment nor a non-empty `.skip`.

## Release time

Before tagging, combine the fragments into `CHANGELOG.md` and delete them in
one commit:

```bash
python tools/changelog_fragments.py combine
git add -A CHANGELOG.md changelog.d && git commit -m "Combine changelog fragments"
```

Fragments are inserted at the top of `## [Unreleased]` in file-name order.
`.skip` files are deleted without being copied. That combine commit is the
only change allowed to edit `CHANGELOG.md`; it is made on `master`, not in a PR.
