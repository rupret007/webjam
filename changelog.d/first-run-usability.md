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
  `direct-stun` (public STUN, simultaneous UDP hole punch, clipboard-sealed
  candidates, no port forwarding, $0). After Join, the friend sends a short
  reply back and the host presses Paste Reply. Source checkouts without the
  sidecar stay on LAN. When the direct pair fails, Host/Join uses a $0 TURN
  relay (self-hosted coturn or Cloudflare Realtime). The HUD still says
  WebJam couldn't reach the host if both paths fail. The operator plan
  remains on #372 until it fits a later slice.
