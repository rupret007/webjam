- Metadata backup preview compares two bounded captures from the same open
  file before its existing final metadata checks, rejecting persistent edits
  with unchanged timestamps. Completed previews retain their reviewed bytes.
