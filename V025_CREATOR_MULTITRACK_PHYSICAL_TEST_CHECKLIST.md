# WebJam v0.25.0 creator-multitrack physical test checklist

> Status: **NOT RUN**. v0.25.0 is an unpublished source candidate. GitHub
> **Latest** remains immutable v0.24.0. This ledger may be filled only with
> observations from exact future v0.25.0 release assets whose SHA-256 values
> match their published checksum manifest.

No v0.25.0 release ID, tag object, tag commit, CI run, asset ID, asset size,
asset hash, checksum-manifest hash, body hash, inventory hash, or physical PASS
evidence exists yet. Do not copy v0.24.0 evidence, use a branch artifact, or
infer a hardware result from automated tests.

Use `PASS`, `FAIL`, or `NOT RUN` only after recording the exact machine,
interface, headphones, operating system, package filename, SHA-256, app version,
and source build ID. Never record invitation links, meeting links, credentials,
device UIDs, private paths, participant names, or raw exceptions.

## Exact candidate identity

| Evidence | Result |
| --- | --- |
| Annotated `v0.25.0` tag object and peeled commit | **NOT RUN — unpublished** |
| Unique successful tag CI run | **NOT RUN — unpublished** |
| Draft and published release ID | **NOT RUN — unpublished** |
| Release body and sorted inventory SHA-256 | **NOT RUN — unpublished** |
| Windows Setup/ZIP names, sizes, IDs, and SHA-256 | **NOT RUN — unpublished** |
| Intel Mac DMG/ZIP names, sizes, IDs, and SHA-256 | **NOT RUN — unpublished** |
| Apple-silicon Mac DMG/ZIP names, sizes, IDs, and SHA-256 | **NOT RUN — unpublished** |
| Ubuntu 22.04 x64 ZIP name, size, ID, and SHA-256 | **NOT RUN — unpublished** |
| `WebJam-v0.25.0-SHA256SUMS.txt` identity and seven verified entries | **NOT RUN — unpublished** |
| Physical client/server Jamulus identity and package build IDs | **NOT RUN — unpublished** |

## A. Package and clean-start boundary

| ID | Physical / packaged observation | Result |
| --- | --- | --- |
| A01 | Windows x64 Setup installs per-user from the exact unsigned candidate and launches from Start | **NOT RUN** |
| A02 | Windows x64 portable ZIP launches after fresh extraction and matches the Setup build identity | **NOT RUN** |
| A03 | Intel Mac exact DMG drag-to-Applications path launches through Apple's Open Anyway flow | **NOT RUN** |
| A04 | Apple-silicon Mac exact DMG drag-to-Applications path launches through Apple's Open Anyway flow | **NOT RUN** |
| A05 | Both Mac portable ZIPs launch after fresh extraction without Full Disk Access | **NOT RUN** |
| A06 | Ubuntu 22.04 x64 ZIP launches after fresh extraction with its bundled files intact | **NOT RUN** |
| A07 | Windows SmartScreen/managed-device behavior is recorded without a publisher-trust claim | **NOT RUN** |
| A08 | Mac Gatekeeper, ad-hoc signature, and unnotarized status are recorded without a production-trust claim | **NOT RUN** |

## B. Creator-profile launch and local scratchpads

| ID | Physical / packaged observation | Result |
| --- | --- | --- |
| B01 | Launch asks what the user is creating and visibly marks Review & Rehearsal Preview | **NOT RUN** |
| B02 | Music shows Host/Join/New Music Project and Band Check language | **NOT RUN** |
| B03 | Podcast & Voice shows Host Remote Recording/Join Recording/New Local Recording and Sound Check language | **NOT RUN** |
| B04 | Review shows Host Review/Join Review, Session Check (Preview), and no enabled standalone project | **NOT RUN** |
| B05 | Switching Music, Podcast, and Review saves/loads three separate local-only scratchpads | **NOT RUN** |
| B06 | Scratchpads survive restart, remain private, and never appear on another participant or in a Support Bundle | **NOT RUN** |
| B07 | Legacy unprofiled project, take, session metadata, and preferences open as Music | **NOT RUN** |

## C. Live WebJam audio and meeting-platform boundary

| ID | Physical / packaged observation | Result |
| --- | --- | --- |
| C01 | Two exact Mac packages Host/Join Music through Jamulus with wired headphones and audible separation | **NOT RUN** |
| C02 | Podcast host and guest hear WebJam-path speech clearly and see speaker/microphone vocabulary | **NOT RUN** |
| C03 | Review host and guest use live WebJam audio and retain the visible Preview boundary | **NOT RUN** |
| C04 | A known Webex, Zoom, Teams, Meet, or FaceTime public-HTTPS link uses one explicit external handoff | **NOT RUN** |
| C05 | Another accepted public-HTTPS DNS-host meeting provider remains neutral and uses the same handoff | **NOT RUN** |
| C06 | Credentials, custom ports, IP literals, and local/special-use or lookalike hosts fail closed | **NOT RUN** |
| C07 | Native detection/activation/mute guidance remains Webex-only and never claims join or mute | **NOT RUN** |
| C08 | WebJam does not directly or automatically tap a meeting app, browser, or system output; explicitly selected Local Original inputs are documented and are not fed meeting/system audio during this test | **NOT RUN** |

## D. Shared Track and authoritative recording plan

| ID | Physical / packaged observation | Result |
| --- | --- | --- |
| D01 | Host loads a supported Shared Track without playback and Play remains locked until exact route proof | **NOT RUN** |
| D02 | Two participants hear the routed Shared Track as a separate Jamulus participant with independent level | **NOT RUN** |
| D03 | Record Session freezes the exact roster/server stems and expected source count before capture | **NOT RUN** |
| D04 | The plan binds the exact Shared Track fingerprint and playback generation used by the take | **NOT RUN** |
| D05 | A replaced or regenerated Shared Track cannot finalize as the planned source | **NOT RUN** |
| D06 | One configured mono row produces exactly one mono PCM-24 Local Original | **NOT RUN** |
| D07 | One configured stereo row produces exactly one true two-channel PCM-24 Local Original | **NOT RUN** |
| D08 | Multiple mono/stereo rows map to the intended adjacent interface channels without swaps | **NOT RUN** |
| D09 | Opting out every configured row produces no host Local Original | **NOT RUN** |
| D10 | A genuinely empty legacy map produces only the documented two-mono default | **NOT RUN** |
| D11 | Guest count/map obligation is frozen before host capture and exact delivery finalizes | **NOT RUN** |
| D12 | Guest under-delivery, over-delivery, changed topology, or source substitution fails closed | **NOT RUN** |
| D13 | Guest reconnect/presence-generation change cannot masquerade as the frozen obligation | **NOT RUN** |
| D14 | Stop passes through Stopping and Finalizing; only exact verified sources permit Ready | **NOT RUN** |

## E. Recovery, Studio, and export

| ID | Physical / packaged observation | Result |
| --- | --- | --- |
| E01 | Interrupted mono/stereo local capture reopens with its exact logical topology and declared gaps | **NOT RUN** |
| E02 | A recovered stereo source remains one two-channel track in take review and playback | **NOT RUN** |
| E03 | Music completed-take Studio permits edit, comp, mix mutation, and eligible export | **NOT RUN** |
| E04 | Podcast completed-take Studio preserves episode/reference-audio/speaker vocabulary and permits editing/export | **NOT RUN** |
| E05 | Review completed-take Studio plays in read-only review and blocks edit, comp, mix mutation, and export | **NOT RUN** |
| E06 | Studio and exported stems preserve a true stereo Local Original as two channels in one file | **NOT RUN** |
| E07 | Declared gaps silence both channels of a stereo source without changing its track identity | **NOT RUN** |
| E08 | Source checksum/topology/manifest substitution fails closed before playback or export | **NOT RUN** |
| E09 | macOS/Linux evidence-rich export imports into the named external editor with exact stem alignment | **NOT RUN** |
| E10 | Windows aligned-originals fallback states its exclusions and never claims edited-package parity | **NOT RUN** |

## F. Resilience, layout, and accessibility

| ID | Physical / packaged observation | Result |
| --- | --- | --- |
| F01 | Interface disconnect/reconnect during a take preserves honest recording and recovery state | **NOT RUN** |
| F02 | Network interruption and guest return preserve source identity without duplicate stems | **NOT RUN** |
| F03 | Sleep/wake, app interruption, and repeated Stop/cleanup do not create a false Ready take | **NOT RUN** |
| F04 | A long multitrack session completes without dropped UI ownership or unbounded storage growth | **NOT RUN** |
| F05 | Launch, live, recording, and Studio layouts remain usable at 720×560, 760×600, 1024×768, and 1440×900 | **NOT RUN** |
| F06 | Keyboard-only operation reaches profile, Host/Join, Record/Stop, take review, and permitted Studio actions | **NOT RUN** |
| F07 | Screen reader announces profile tier, recording phase, logical mono/stereo tracks, and Review blocks without color-only meaning | **NOT RUN** |
| F08 | High-DPI, long labels, and Podcast/Review vocabulary do not clip critical actions or status | **NOT RUN** |

## Release decision summary

| Gate family | Result | Blocking evidence |
| --- | --- | --- |
| Exact release identity | **NOT RUN** | Unpublished |
| Windows package/install | **NOT RUN** | None recorded |
| macOS package/install | **NOT RUN** | None recorded |
| Linux package/run | **NOT RUN** | None recorded |
| Creator profiles | **NOT RUN** | None recorded |
| Local scratchpad privacy | **NOT RUN** | None recorded |
| Live WebJam audio | **NOT RUN** | None recorded |
| Meeting-platform boundary | **NOT RUN** | None recorded |
| Shared Track | **NOT RUN** | None recorded |
| Authoritative mono/stereo multitrack | **NOT RUN** | None recorded |
| Guest Local Original obligations | **NOT RUN** | None recorded |
| Recovery/finalization | **NOT RUN** | None recorded |
| Studio/export profile gates | **NOT RUN** | None recorded |
| Accessibility/layout | **NOT RUN** | None recorded |
| Signing/notarization/platform trust | **NOT RUN** | None recorded |

Release recommendation: **NOT RUN**. Do not promote or describe v0.25.0 as
physically validated from this blank ledger.
