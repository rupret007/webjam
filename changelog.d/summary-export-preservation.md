- Library **Export summary** no longer deletes the chosen file after a write
  or sync failure, so another writer's replacement stays intact. A failed
  export says the file may be incomplete and asks for a new filename.
  Existing destinations are still never overwritten.
