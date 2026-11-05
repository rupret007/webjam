- Hosts keep waiting for a guest reply through temporary signaling outages,
  including interrupted responses. Polling and WebSocket wakeups share the
  remaining wait budget, and cancellation interrupts backoff and receive waits.
- Cancelled private-connection control transfers release their QUIC streams
  promptly, allowing later transfers on the same connection to proceed.
- Added regression coverage for signaling recovery, timeout and cancellation,
  stalled authenticated streams, and blocked direct connections without TURN.
