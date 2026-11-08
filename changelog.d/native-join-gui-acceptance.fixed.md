- Add a guarded developer check for the installed Mac Join screen: preserve
  invitation masking, verify malformed-link recovery and clearing, and refuse
  Host/media actions. Its fixture is checked against production parsing so a
  DNS-shaped legacy endpoint cannot accidentally be used as malformed input.
