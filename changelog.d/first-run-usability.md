- First-run screens now speak to a non-technical musician or artist: non-Mac
  Music doors ghost Host and keep Join as the one orange; Copy Invite keeps a
  persistent HUD card (“Copied. Send the whole message to your friend. Keep
  this window open.”) and names same-network only as a LAN limitation; Paint
  along has one orange and a door line that matches Watch a shared lesson;
  Music Host leads with “Play a note…”; bad pastes and used invites use plain
  English; the host sees “Alex is here”; idle copy and Art overview both say
  Start Session; Paint along titles the room; Art Settings hides song-tool
  keys.
- Cross-network Host/Join: packaged apps with `webjam-fabric` use compiled
  `direct-stun` (public STUN, clipboard-sealed candidates, no port forwarding,
  $0). Source checkouts without the sidecar stay on LAN. Symmetric NAT / CGNAT
  / cellular still fail without a later relay; the HUD says so instead of
  faking a room. Plan: `docs/REMOTE-SESSIONS-PLAN.md`.
