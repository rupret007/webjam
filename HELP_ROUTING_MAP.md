# WebJam help routing — v0.24.0 private test release

> GitHub **Latest** is immutable v0.24.0. These recording-first labels target
> the exact checksum-verified private test release; physical results remain
> **NOT RUN** until observed against those packages.

| Musician says | Answer / action |
| --- | --- |
| “I can’t find my interface.” | Open **More → Audio Settings in Jamulus**. Choose it in Jamulus, not WebJam. |
| “I need to change buffer or channels.” | Use Jamulus Audio/Network Settings. |
| “Where is the Start Session button?” | Host or Join already starts the required session work. Set up Jamulus; WebJam moves into the session automatically after it sees the connection. |
| “Why did a meeting open in my browser or app?” | Only **Join / Open Meeting** hands off a saved public HTTPS meeting link. Known Webex, Zoom, Teams, Google Meet, and FaceTime links receive friendly labels; another accepted provider stays neutral. WebJam does not claim the service joined or that an unknown provider was natively verified. On macOS, the separate Webex-only **Show Webex App** action re-verifies Cisco's app and never passes it the link. |
| “Can WebJam mute Webex?” | No. On macOS, **Open Webex to Mute** shows the verified app so the musician can use Webex's own Mute control. WebJam never claims mute success or touches Jamulus. |
| “Why is Show Webex App unavailable?” | Direct native activation requires verified publisher identity. Current Windows/Linux packages use **Join / Open Meeting** because that proof is unavailable there. |
| “Where is Studio?” | Use the direct **Studio** action or Cmd/Ctrl+3. Studio is intentionally absent from More. |
| “Why will my Shared Track not play?” | Source loading and playback permission are separate. WebJam proves the isolated local BlackHole route before enabling Play; installing BlackHole, running setup, or choosing **Recheck Route** never bypasses that proof. Loading still validates the file and decodes its first bounded block. |
| “Can I replace the Shared Track while it plays?” | No. Stop it first. **Replace…** and **Remove** stay unavailable until the active route is safely stopped; cleanup pending remains visible and retryable. |
| “Can guests control the Shared Track?” | No. The host owns the transport. Guests receive bounded, path-free host state and can adjust `WebJam Track` in their Jamulus mix; older peer state may show only channel presence. Neither is synchronization or audibility proof. |
| “Do I need recording setup to play?” | No. Recording is optional and appears only at **Record Session** time. |
| “Why is the take still finalizing?” | Stop is not completion. Keep WebJam open while it verifies media and publishes the take. Open Studio only after **Ready**; use the shown recovery if it says **Needs attention** or cleanup pending. |
| “Where do I choose a Studio speaker?” | Open a take in Studio. Playback output is a Studio choice. |
| “How do I combine another take?” | In Studio, select the musician's track, add a safely matched take lane, then Option/Alt-drag the range to comp. |
| “Did Delete erase my recording?” | No. Arrange edits change only the Studio sidecar; the take manifest and WAVs remain recording truth. |
| “What comes with Track Export?” | Equal-length edited/original 24-bit stems, rough mix, markers, instructions, provenance, and checksums. |
| “How do I verify sound?” | Play a note and make sure a bandmate hears you. Use **More → Band Check / Verify Sound** if you need help. |
| “Jamulus closed or lost sound.” | Bring Jamulus forward, fix sound there, then return to WebJam’s safe retry path. |

Do not ask a musician for ports, CoreAudio UIDs, JSON-RPC details, Jamulus
profile filenames, private file paths, or server process information.
