# ADR 0011: Art's AI image action is a Krita handoff, loopback only

- Status: Accepted for the Art Preview; no release or physical evidence
- Date: 2026-08-21
- Scope: The Art creator profile's in-session AI image action

## Context

Artists want two specific things from AI while they work: make a new image from
a description, and fix or extend a photo they already have. Both are ordinary
parts of a working session now.

WebJam could build that. It would need a diffusion runtime, model management,
schedulers, ControlNet and IP-Adapter plumbing, masking, inpaint and outpaint,
and a UI for prompts, seeds, and strengths. Together that is an image
generation application, and WebJam is a session conductor.

Krita AI Diffusion already is that application. It is a Krita plugin, actively
maintained, with generation, inpaint, outpaint, object removal, and photo
editing; it drives ComfyUI as its backend, and it installs and manages a local
ComfyUI itself so an artist does not have to. Krita is also, separately, the
best open-source painter for finishing a piece.

The precedent is now well established in this product: WebJam does not
implement low-latency audio (Jamulus does), and it does not implement a
collaborative canvas (Drawpile does, ADR 0010).

## Decision

Art gains **one in-session action with two verbs**: **Make** and **Edit**.
WebJam finds Krita, checks its AI plugin is installed, opens Krita, and stops.

**Not a start card.** Art's start screen stays at three cards. Nobody decides
what they are making by choosing an image generator: they decide to talk, to
paint together, or to paint along, and reach for AI partway through. The
capability is therefore expressed only as an in-session action, and
`_validate_creator_registry` refuses a start that expresses AI at all.

**What WebJam does.**

- Finds Krita using the shared rules in `core.external_program`: explicit
  absolute install locations, no `PATH` search, no glob, resolved real
  executable file.
- Checks for the plugin at `<krita-resources>/pykrita/ai_diffusion`, which is
  where the plugin's own installer puts it. Presence is all it checks; it does
  not read the plugin's settings, version, or user data.
- **Make** opens Krita on a fresh 1024×1024 RGBA canvas
  (`--new-image RGBA,U8,1024,1024 --nosplash`), ready for the AI docker.
- **Edit** opens Krita on one local image the artist picked, after verifying it
  is a real, regular, non-symlink local file with a supported suffix.

**What WebJam deliberately does not do.**

- It takes **no prompt**, and has no model list, LoRA browser, sampler, step
  count, seed, or mask tool. Krita owns every one of those. Reproducing any of
  them would be inventing a generator rather than integrating one.
- It does not run, install, or configure ComfyUI. The plugin manages its own
  server, and connects to one already running.
- It does not write Krita's configuration. That folder belongs to Krita and to
  the artist.
- It does not read the shared Drawpile canvas, and does not feed it to any
  model. Any future choice to do so would be an explicit, separate decision,
  not a side effect of this action.

**The network boundary.** This is the load-bearing security decision, and it
lives in one function, `normalize_local_backend_url`. Only a loopback address
is accepted; anything else — a LAN address, a hostname that merely contains
`127.0.0.1`, an `https` cloud endpoint, a URL with credentials, a path, or a
query — is refused before a request is ever constructed. The refusal applies to
the default, to a value in the saved configuration file, and to
`WEBJAM_COMFYUI_URL`, so an edited config cannot turn this into an upload path.
The only request made is a `GET` to ComfyUI's read-only `/system_stats` with a
short deadline, proxies disabled, and redirects refused.

**Nothing is published.** `core.ai_image` has no publisher, imports no transfer
layer, and `SessionStateSnapshot` gains no AI member. A generated image is
never broadcast as "the room's image". It reaches the room only when its owner
puts it on the shared Drawpile canvas, or when the host later shares a file
they own under the existing reference contract — which remains **video**, under
same-file identity.

**Nobody drives anyone else's generator.** There is no host and no guest in
this path, only "this computer". The controller is not given a role or a peer,
so a host cannot Make on a guest's machine.

**Results belong to the artist.** Krita writes the file where the artist says.
WebJam ships no models and no image catalog, requires no cloud key for the
happy path, and never asks for one.

**Fail closed.** No Krita, or Krita without the plugin, disables both verbs,
says which one is missing, and offers that download. WebJam never opens an
editor to a docker that is not there.

## Alternatives considered

- **Calling a cloud image API.** Rejected. It would require a key, put the
  artist's photos on somebody else's computer, and make the happy path depend
  on a paid service. The loopback rule exists specifically to make this
  impossible by construction rather than by policy.
- **Driving ComfyUI's HTTP API directly from WebJam.** Rejected. WebJam would
  then own a workflow graph, model choices, and a prompt UI — inventing a
  generator. It would also duplicate, worse, what the plugin already does.
- **An image panel inside WebJam showing generated results.** Rejected for this
  pass. It implies WebJam manages the artist's output, and the honest model is
  that the file is theirs, in Krita, on their disk.
- **Offering a launch menu of creative tools** (Krita, GIMP, Inkscape, Blender,
  OpenToonz). Rejected. That is a tool picker masquerading as a feature. Krita
  is opened for one named reason, from one named action.
- **Making AI a fourth start card.** Rejected, per the reasoning above.

## Consequences

Art gains real generation and real photo editing — with brushes, masks, models,
and inpaint that WebJam could not have built — without WebJam claiming to
generate anything and without a single byte leaving the machine. The cost is
two dependencies the artist installs themselves and a handoff they can see
happening. Both are stated in the UI rather than hidden.

Two-computer behavior is **NOT RUN**. This contract is covered by automated
tests only. The handoff has not been exercised against a real Krita install,
and the AI path has never met a real ComfyUI backend.

## References

- [Krita AI Diffusion](https://github.com/Acly/krita-ai-diffusion)
- [Krita AI handbook: paths and common issues](https://docs.interstice.cloud/common-issues/)
- [Krita AI handbook: custom ComfyUI setup](https://docs.interstice.cloud/comfyui-setup/)
- ADR 0010: Art's shared canvas as a Drawpile handoff, for the same
  find-launch-conduct boundary
- ADR 0004: external Webex launch, for the original statement of that boundary
