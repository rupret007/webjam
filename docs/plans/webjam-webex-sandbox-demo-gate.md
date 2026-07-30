# WebJam Webex sandbox demo gate

Status: **NOT RUN**

> **Unreleased after v0.22.2:** this maintained worksheet uses source behavior
> not present in the immutable published v0.22.2 packages.

This worksheet validates WebJam's external Webex handoff without storing a
Webex username, password, token, admin address, or private meeting path in the
repository or evidence bundle.

## Before testing

1. Rotate every password that was shared in chat before using the sandbox.
2. Use the ordinary sandbox participant for meetings. Use the site
   administrator only when a site policy must be changed.
3. Use the exact packaged WebJam candidate intended for the demo. Do not infer
   package results from a source-tree run.
4. Connect wired headphones at both musician endpoints.
5. Record these non-secret identity facts:

| Fact | Evidence |
| --- | --- |
| WebJam version | NOT RUN |
| Build ID | NOT RUN |
| Package filename | NOT RUN |
| Package SHA-256 | NOT RUN |
| macOS/Windows version and architecture | NOT RUN |
| Webex client/browser version | NOT RUN |
| Jamulus version | NOT RUN |

Never paste the meeting path, credentials, cookies, tokens, or participant
email into this worksheet.

## Configuration and validation

Use the private Personal Room link locally, but identify it in evidence only by
the derived `*.webex.com` site hostname.

| Check | Result | Evidence/notes |
| --- | --- | --- |
| Settings accepts a valid HTTPS Webex Meeting or Personal Room link | NOT RUN | |
| Reopening Settings preserves the link | NOT RUN | |
| Settings shows only the derived Webex hostname | NOT RUN | |
| Malformed input is rejected | NOT RUN | |
| An HTTP Webex link is rejected | NOT RUN | |
| A non-Webex HTTPS link is rejected | NOT RUN | |
| Direct **Webex Controls** and **More → Webex Controls** show the same Conversation panel without a launch | NOT RUN | |
| On macOS, **Show Webex App** dynamically re-verifies the exact running Cisco PID and requests activation without opening a browser or meeting | NOT RUN | |
| A minimized-window check records what the musician sees without claiming WebJam can prove restoration | NOT RUN | |
| On macOS with Webex stopped, **Show Webex App** refuses without launching; guidance says to open Webex manually or use Join/Open | NOT RUN | |
| Windows/Linux native focus stays unavailable without publisher proof | NOT RUN | |
| **Join / Open Meeting** opens the intended native Webex app or browser destination exactly once | NOT RUN | |
| WebJam says “Opened externally—finish joining in Webex” and never claims it joined | NOT RUN | |

## Two-endpoint meeting

Join from the WebJam computer and a second endpoint, preferably an iPhone
running Webex.

| Check | Result | Evidence/notes |
| --- | --- | --- |
| Second endpoint is admitted | NOT RUN | |
| Both participants are present | NOT RUN | |
| Camera works in both directions | NOT RUN | |
| Speech works in both directions | NOT RUN | |
| Mute, leave, and rejoin work | NOT RUN | |
| Webex identity comes from Webex, not the WebJam musician name | NOT RUN | |

## Jamulus coexistence

Keep Webex muted while playing music. Monitor the music only through Jamulus.

| Check | Result | Evidence/notes |
| --- | --- | --- |
| Jamulus remains connected while Webex is open | NOT RUN | |
| Jamulus audio is clean through wired headphones | NOT RUN | |
| Webex creates no duplicated or feedback path | NOT RUN | |
| Webex does not replace the selected Jamulus devices | NOT RUN | |
| Closing/failing Webex does not end Jamulus | NOT RUN | |
| Quitting WebJam does not claim it closed externally owned Webex | NOT RUN | |

## Privacy and teardown

| Check | Result | Evidence/notes |
| --- | --- | --- |
| WebJam logs contain no password, token, or private meeting path | NOT RUN | |
| A saved support bundle contains no password, token, or private meeting path | NOT RUN | |
| Support material may retain only the Webex site hostname | NOT RUN | |
| All WebJam-owned processes and ports stop cleanly | NOT RUN | |

## Decision

Block the demo for a crash, wrong Webex destination, inability to join from a
real second endpoint, feedback under the documented wired-headphone setup, or
any Jamulus interruption. Record lesser presentation issues as follow-up work.

Final verdict: **NOT RUN**
