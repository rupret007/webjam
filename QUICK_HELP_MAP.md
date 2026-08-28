# WebJam quick help — post-v0.27.1 source

> These labels describe current post-v0.27.1 source. GitHub **Latest** is the
> unsigned/ad-hoc v0.27.1 private test release. No physical PASS is claimed;
> every v0.27 physical and release-decision row remains **NOT RUN**.

| Need | Use |
| --- | --- |
| Choose a creator workflow | Launch → **Art** or **Music**. Art is **Talk & make** / **Paint together** / **Paint along**, then **Host** / **Join**. Music is **Host** / **Join** only |
| Start a live session | Choose **Art** or **Music**, then **Host**; Art chooses a start card first |
| Join a live session | Choose a profile → **Join**, **Join Recording**, or **Join Review** → paste one invite; the invite carries whatever the host started |
| Talk while making art | Art → **Talk & make** → **Host** or **Join**; a room with no canvas and no video is normal and needs no setup |
| Paint on one shared canvas | Art → **Paint together** → **Host**, then **More → Shared Canvas… → Host in Drawpile**; keep the Drawpile session **Personal** and paste its invitation back into WebJam |
| Join the shared canvas | Art guest → **More → Shared Canvas… → Open shared canvas**; Drawpile must be installed, and WebJam says so plainly when it is not |
| See where the room is while painting | Art → **More → Shared Canvas…**; the panel shows **Bar 17.3 · Chorus**, a video position, or **No shared clock**. It is a readout, not a control |
| Make an image with AI | Art, in session → notes **Suggestion** → **Make**; Krita opens a new canvas and its AI Image Generation docker takes your prompt. WebJam generates nothing and uploads nothing |
| Edit a photo with AI | Art, in session → notes **Suggestion** → **Edit…** and pick an image you own; Krita opens it for fill, extend, or remove |
| Start a process video | Art host → **Paint along → Choose process video…** and pick one local file you have the right to play; paint in your usual app or on paper beside WebJam |
| Follow the host's process video | Art guest → **Paint along → Open my copy…** and pick your own copy of the host's exact file; a different file is refused rather than played |
| Ignore a process video | Art → **Paint along → Hide video**; you stay in the room and in the conversation |
| Start a supported local project | Music → **New Music Project** or Podcast & Voice → **New Local Recording** |
| Change live sound | **More → Audio Settings in Jamulus** |
| Invite a collaborator | Host setup → **Copy Invite** |
| Show conversation controls | Direct **Conversation** or **More → Conversation**; this does not open a meeting |
| Show Webex on Mac | Conversation → **Show Webex App**; this activates or launches the verified app itself without a meeting link, while Webex chooses its own screen |
| Open a saved meeting from any platform | Conversation → **Join / Open Meeting**; the link must be public HTTPS with a DNS hostname |
| Record meeting-app audio | Use the meeting service's own recorder. WebJam never directly or automatically taps a meeting app, browser, or system output. Local Originals record explicitly selected input devices, so do not route meeting or system-output audio into them. |
| Copy or change the meeting link | Conversation → **Copy Link** or **Change Link** |
| Update Jamulus | **More → Jamulus Updates…** |
| Add a host Shared Track | Live session → **Add Shared Track** or drop one supported local file; loading validates and decodes a first bounded block but does not start playback |
| Play a loaded Shared Track | If the strip says **Set up the audio device**, open that badge → **Set Up Shared Track…** → install official BlackHole 16ch or 64ch at 48 kHz → **Recheck Route**. No signed catalog is required. When the route is ready, choose Play |
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
| Open Song tools | Music live session → direct **Song** control beside Studio, or Cmd/Ctrl+4; it is intentionally absent from More |
| Write the song down | Session notes are the sheet: `Key: G major`, `Tempo: 120`, `[Verse x8]`, then chords and lyrics under each part |
| Get chords for a part | **Song → Suggestion** on that part; read the reasoning, **Keep** writes it into your notes and **Dismiss** writes nothing |
| Get help with the next section | **Song → Suggestion** on **Next part**; runs on this computer, nothing is uploaded |
| Add a Music AI key | Create one at <https://music.ai/dash> → paste into **Settings**, or set `MUSIC_AI_API_KEY`; a Moises app login is a different account |
| Split stems | **Song → Stems → Split a file you own**; host only, always confirms the file |
| Mute the record's vocal and sing it | **Song → Stems → Sing this one**; these chips are the reference file, not the band, and musician faders are unchanged |
| Send stems into the jam | **Song → Stems → Send to jam**; routes through the host-owned Shared Track, mixing several stems to one file first |
| Know which app owns which device | Meeting platform uses the computer mic/speakers for faces; Jamulus uses your interface; Song tools never re-bind either |
| Show chords to the room | They live on the WebJam strip. Screen-sharing your desktop into a meeting is your own choice, not a WebJam feature |
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
