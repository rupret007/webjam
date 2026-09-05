# Worth-Building — Art room participation

2026-09-05 CT. Branch `codex/webjam-art-participant-door`, based on
`origin/master` at `7f38ba20eb71afffdb37a8d03d29248abfdee1de`.

**Source/product Worth-Building: PASS.**
The final commit, hosted results, and lease release are recorded in the draft PR
and Bob-the-Bot #3 AFTER. Historical #68 results are not proof for this slice.

The product gap was concrete: an artist accepting a room invitation followed
Music startup, while native guests had no host profile or creative state to
follow. Art now has a room participant that can enter, follow, recover, and
leave without constructing a recording guest or starting Music audio.

| Gate | Product behavior and evidence |
| --- | --- |
| Clear start | Exactly Make together / Paint along, then Host / Join; Music retains Host / Join. Repeated click and Space preserve selection, white focus stays distinct from burnt-orange selection, and the neutral squirrel mark retains its size. `test_art_participant_door.py`, `test_art_start_ux.py`. |
| Real room entry | Private LAN discovery reads the authenticated host profile before choosing Art or Music. Native peers deliver typed initial and live room state. Actual controller tests enter Art from a saved Music profile without Jamulus or a recording owner. `test_art_room_controller.py`, `test_art_room_connection_facts.py`. |
| Useful collaboration | Existing silent local-video matching/follow and optional canvas owners consume current host state; own tools and Webex demonstrations remain available. Wrong local files, withdrawal, stale state, and invitation replacement stop following honestly. No automatic external app launch. |
| One recovery action | Waiting, connected, reconnecting, update/rejoin, End Room / Leave Room, and cleanup retries use observed ownership. Music audio evidence, unresolved recording, local notes, and unfinished cleanup retain precedence. `test_art_audio_cleanup.py`, `test_art_room_recovery.py`. |
| Private invitation | Full invitation validation happens on Join; replacing a paste clears accessible errors. Existing capability/pinned connection boundaries remain. Typed room payloads are bounded, ephemeral, and excluded from diagnostics and persistence. `test_room_state.py`, native transport and Go room tests. |
| Real transport proof | One reliable dispatcher handles Help and room state. Two actual sidecar processes prove initial/live state, withdrawal, reset with a fresh guest, stale rejection, independent rate limits, and connection loss. Existing audio datagrams and Help still work. `test_native_room_process.py`, `test_native_sidecar_integration.py`. |
| Product honesty | Guides describe Art as a newer Preview for artists across mediums and distinguish room entry from Music audio setup. No public service, physical output, signing, or release result is inferred from source automation. |

Required proof: focused regressions, full unfiltered local pytest, Ruff,
compileall, pip check, UX smoke, Go checks/race suite, Pre-Karen review, and
hosted SUCCESS on the exact open draft tip including all four desktop builds.
No source from parked #37/#49 or the #67 branch is a work target.
