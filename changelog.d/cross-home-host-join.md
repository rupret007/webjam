- Ordinary Host/Join is a friend in another house, with no extra setup.
  Packaged Host now starts compiled ``direct-stun``. Music Host stays
  Mac-only. Windows **More info → Run anyway** and Mac **Open Anyway**
  stay in the start steps. Paste the invite to Join; the host already
  accepts on direct and relay. The guest reply still travels as a
  clipboard paste (``publish_guest_reply`` / ``accept_guest_reply``)
  so that path can be replaced later without changing Host start. If Join cannot
  connect, use home Wi-Fi on both computers, not a phone hotspot, and
  ask for a fresh invite. TURN credentials start in parallel with STUN,
  are skipped on a LAN-only host, and wait 12 seconds (10-second Worker
  worst case plus 2 seconds of headroom).
