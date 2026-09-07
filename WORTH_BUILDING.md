# Worth Building — Paint along timeline input moves the video

On exact post-#85 master `12920ebe35b96298e2c2b97fdcf78efc1d1720f7`, a host
can use arrow keys, Home/End, Page Up/Down or the wheel to move the Paint along
slider, but the video and published room position do not move. The next
playback update can snap the slider back. Real Qt input fixtures reproduced
10 failures before this fix, including a coordinator/player/peer journey.

Keyboard and wheel changes now commit through the existing seek operation.
Rendering never emits intent. A mouse drag commits once on release, including
on native macOS where the style can change the value before sliderPressed.
A replaced/failed/unshared video or navigation cancels the old drag. Hosts keep
existing ready, playing and paused behavior; guests receive no seek control.

This is one Art interaction fix, not another transport or video stack. It
keeps silent local-file Paint along beside external conversation, with no new
launch, publication format, logging payload, room claim, door copy or asset.

The 21 input fixtures use real Qt keyboard/wheel/mouse events and synthetic
player/peer fixtures. A real held-groove fixture also reproduced stale auto-repeat seeks after a
source replacement; cancellation now stops that repeat. The coordinator journey renders synchronous snapshots
back into the same dialog and verifies one seek, one publication, updated
clock and unchanged playback/mute state. Physical/video-device and two-machine
behavior remain NOT RUN. Final suite and hosted proof belong in the draft.

Checkout: `/Users/jeffstory/Documents/WebJam`. Branch:
`codex/webjam-finish-product-paint-along-seeking`. BEFORE:
https://github.com/rupret007/Bob-the-Bot/issues/3#issuecomment-5563756244.

The earlier logging improvement is preserved in a separate local checkpoint
`94c621e36ebe3ff7e2748b91d8a968021c30da92`; it is not part of this Art draft.
#85/#84 stay merged. Parked #37/#49 and the Jeff-only release boundary remain.
