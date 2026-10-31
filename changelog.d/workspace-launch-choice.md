- Refuse incomplete workspace launch choices before creating a window,
  controller or background service. An accepted dialog without an explicit role,
  or Join without exactly one typed invitation, cannot fall through to startup
  using saved host defaults. Existing valid Host/Join and offline routes retain
  their behavior; this does not change platform trust or capture permissions.
