- Metadata backup preview now compares two bounded reads from the same open
  file, rejecting persistent edits even when the filesystem reports unchanged
  timestamps. Completed previews continue to retain their reviewed contents.
