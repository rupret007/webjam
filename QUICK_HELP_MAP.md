# WebJam quick help — v0.26.0 source candidate

> These labels describe the current unpublished v0.26.0 source candidate.
> GitHub **Latest** remains immutable v0.25.0; no v0.26.0 package or physical
> PASS is claimed, and every v0.26 physical row remains **NOT RUN**.

| Need | Use |
| --- | --- |
| Choose a creator workflow | Launch → **What are you creating?** → Music (GA), Podcast & Voice (GA), or Review & Rehearsal (Preview) |
| Start a live session | Choose a profile → **Host**, **Host Remote Recording**, or **Host Review** |
| Join a live session | Choose a profile → **Join**, **Join Recording**, or **Join Review** → paste one invite |
| Start a supported local project | Music → **New Music Project** or Podcast & Voice → **New Local Recording** |
| Change live sound | **More → Audio Settings in Jamulus** |
| Invite a collaborator | Host setup → **Copy Invite** |
| Show conversation controls | Direct **Conversation** or **More → Conversation**; this does not open a meeting |
| Show Webex on Mac | Conversation → **Show Webex App**; this activates or launches the verified app itself without a meeting link, while Webex chooses its own screen |
| Open a saved meeting from any platform | Conversation → **Join / Open Meeting**; the link must be public HTTPS with a DNS hostname |
| Record meeting-app audio | Use the meeting service's own recorder. WebJam never directly or automatically taps a meeting app, browser, or system output. Local Originals record explicitly selected input devices, so do not route meeting or system-output audio into them. |
| Copy or change the meeting link | Conversation → **Copy Link** or **Change Link** |
| Update Jamulus | **More → Jamulus Updates…** |
| Add a host Shared Track | Live session → **Add Shared Track** or drop one supported local file; loading validates and decodes a first bounded block but does not start or unlock playback |
| Replace or remove a Shared Track | Stop playback first → **Shared Track** → **Replace…** or **Remove** |
| Record the live session | Host → **Record Session** |
| Prove the recording plan | **Record Session Readiness** → inspect every server, Local Original, and Shared Track row, mono/stereo format, required/optional status, storage, and blockers → **Start Recording** only when enabled → required guests open and ACK exact streams before Jamulus recording starts |
| Configure local interface stems | **Recording Setup → Edit Input Tracks…**; add named mono/stereo tracks totaling up to 32 enabled input channels |
| Keep a stereo source together | Add one stereo row; it becomes one two-channel PCM-24 Local Original through Studio/export |
| Keep local interface stems | First host **Record Session** → **Also Keep This Mac’s Inputs** |
| Finish a take | **Stop Recording** once → wait through **Finalizing** → require **Ready** |
| Review a take | Direct **Studio** action |
| Select a review source/output | Open a take in Studio |
| Arrange a take | Music or Podcast & Voice Studio → drag/trim a region; use Undo/Redo if needed |
| Comp another recording | New Music/Podcast takes stack exact stable-ID matches automatically; or select track → **＋ Add Take** → Option/Alt-drag |
| Finish a voice episode | Podcast & Voice local project → record/overdub at 48 kHz → add chapters → save/reopen → **Bounce Episode** for stereo PCM-24 WAV |
| Export for another editor | Music or Podcast & Voice Studio → **Track Export** |
| Pair the owner-device iPhone preview | **More → Use iPhone as Pocket Stage…** |
| Verify a live session | Music **Band Check**, Podcast **Sound Check**, or Review **Session Check** |
| End safely | Use the profile's **End** or **Leave** session action |

Jamulus carries WebJam's live music, voice, or review-audio path. Any meeting
platform with an accepted public HTTPS link is optional conversation/video.
Known Webex, Zoom, Teams, Meet, and FaceTime destinations receive friendly
labels; other providers stay neutral. Music and Podcast & Voice Studio provide
a Logic-like arrangement and review experience, not Logic integration; Review
Preview is playback-only. Studio edits never rewrite the take manifest or
source recordings. On Windows and
Linux, current WebJam packages cannot verify the installed Webex publisher, so
native focus actions stay unavailable and **Join / Open Meeting** is the
supported handoff.
