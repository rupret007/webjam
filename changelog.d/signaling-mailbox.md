- Guest reply delivery for ordinary Host/Join is a one-invite mailbox on
  the existing TURN Worker. The guest posts automatically after one
  invite paste; the host receives automatically. Copy reply and Paste
  reply stay the visible fallback when the mailbox is missing. The
  packaged origin is https://webjam-turn-credentials.jeffstory007.workers.dev
  (`WEBJAM_SIGNAL_MAILBOX_URL` overrides). The Worker stores only an
  HKDF mailbox id and AEAD ciphertext (never the capability or
  plaintext). Not deployed from this change.
