# WebJam hosted server — v0.16

On macOS, a host choosing **Host a Jam** authorizes WebJam to supervise the
bundled JamulusServer for that private session. WebJam verifies owned-process
and recorder control facts before it presents an invitation, then launches a
visible Jamulus client against the local server.

The musician never needs server ports or command-line flags. WebJam owns safe
start/stop, invitation lifecycle, recorder authorization, and cleanup. Jamulus
owns the server’s music behavior and the client’s live audio configuration.

WebJam does not start recording at host time. Recording readiness is checked
only when the host presses Record. Before ending a hosted jam, WebJam asks the
recorder to finalize a take; it does not knowingly stop the server underneath
an active recording.

This document describes the in-app private-host workflow. Advanced manual
server operation is outside the musician flow and is not a substitute for the
v0.16 package/pilot procedure.
