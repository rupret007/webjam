# WebJam help routing — v0.22.4

> **Published private test candidate:** these answers describe immutable
> v0.22.4, now GitHub Latest.

| Musician says | Answer / action |
| --- | --- |
| “I can’t find my interface.” | Open **More → Audio Settings in Jamulus**. Choose it in Jamulus, not WebJam. |
| “I need to change buffer or channels.” | Use Jamulus Audio/Network Settings. |
| “Where is the Start Session button?” | Host or Join already starts the required session work. Set up Jamulus; WebJam moves into the session automatically after it sees the connection. |
| “Why did Webex open a browser?” | Only **Join / Open Meeting** hands off the saved link. On macOS, **Show Webex App** re-verifies Cisco's app, then activates or launches that exact app with no URL or browser; Webex chooses its own screen. |
| “Can WebJam mute Webex?” | No. On macOS, **Mute in Webex** shows the verified app so the musician can use Webex's own Mute control. WebJam never claims mute success or touches Jamulus. |
| “Why is Show Webex App unavailable?” | Direct native activation requires verified publisher identity. Current Windows/Linux packages use **Join / Open Meeting** because that proof is unavailable there. |
| “Where is Studio?” | Use the direct **Studio** action or Cmd/Ctrl+3. Studio is intentionally absent from More. |
| “Why will my Reference Track not play?” | Source loading and playback permission are separate. Downloaded v0.22.2 locks Play before route scanning. Published v0.22.4 enables it only after WebJam proves the isolated local BlackHole route; installing BlackHole, running setup, or choosing **Recheck Route** never bypasses that proof. Loading still validates the file and decodes its first bounded block. |
| “Do I need recording setup to play?” | No. Recording is optional and appears only at Record time. |
| “Where do I choose a Studio speaker?” | Open a take in Studio. Playback output is a Studio choice. |
| “How do I combine another take?” | In Studio, select the musician's track, add a safely matched take lane, then Option/Alt-drag the range to comp. |
| “Did Delete erase my recording?” | No. Arrange edits change only the Studio sidecar; the take manifest and WAVs remain recording truth. |
| “What comes with Track Export?” | Equal-length edited/original 24-bit stems, rough mix, markers, instructions, provenance, and checksums. |
| “How do I verify sound?” | Play a note and make sure a bandmate hears you. Use **More → Band Check / Verify Sound** if you need help. |
| “Jamulus closed or lost sound.” | Bring Jamulus forward, fix sound there, then return to WebJam’s safe retry path. |

Do not ask a musician for ports, CoreAudio UIDs, JSON-RPC details, Jamulus
profile filenames, or server process information.
