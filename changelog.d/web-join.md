- Add opt-in Web Join for one browser guest in a desktop-hosted Jamulus session:
  Pion DTLS enrollment, sealed signaling, public/allowlisted ICE with TURN fallback,
  free-tier quota closure, a host-local Opus/Jamulus bridge, and session-scoped
  permission and kick controls. Requires the optional custom-mode Opus library
  and reviewed static-page/Worker updates; no deployment is part of this change.
- Keep browser invitations bound to the actual credential-limited offer expiry
  and preserve IPv4 connectivity when the host also has IPv6 candidates.
