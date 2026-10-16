Library summary export no longer deletes the chosen pathname after a write or
sync failure, preserving any file another writer has placed there. Failed exports
report that the file may be incomplete and ask for a new filename. Existing
destinations remain protected from overwrite.
