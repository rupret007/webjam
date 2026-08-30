"""Art's in-room AI image action: two verbs, no generator of WebJam's own.

Someone in an Art room can have AI **Make** a new image from text, or **Edit**
a photo they already own. That is the whole feature. It is deliberately one
in-session action rather than a launch workflow, because what a person is
making is chosen once at launch from two cards, and an image generator is
something you reach for partway through a session rather than something you
plan a session around.

WebJam generates nothing. :mod:`core.krita_ai` finds Krita and its AI
Diffusion plugin, this module decides when the two verbs are honest to offer,
and Krita owns every prompt, model, sampler, and mask. There is no model
picker here, no LoRA browser, and no prompt panel, and their absence is the
point: reproducing them would be inventing a generator instead of integrating
one.

Three properties are worth stating because they are structural rather than
copy:

* **Nothing is published.** This module has no peer publisher and no wire
  schema, so a generated image is never broadcast as "the room's image". If
  someone wants the room to see it, they put it on the shared Drawpile canvas
  themselves, or the host uses a file they own under the existing reference
  contract.
* **Nobody drives anyone else's generator.** There is no host and no guest
  here, only "this computer". A host cannot Make on a guest's machine, and the
  controller is not given the ability to try.
* **Nothing leaves the machine.** The backend is loopback-only, enforced in
  :mod:`core.krita_ai`, so no path through this feature can upload an artist's
  photo to somebody else's computer.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Protocol, runtime_checkable

from core.krita_ai import (
    INSTALL_KRITA_MESSAGE,
    INSTALL_PLUGIN_MESSAGE,
    AiImageError,
    AiImageUnavailableError,
    LocalImage,
    load_local_image,
)

NOT_IN_A_ROOM_MESSAGE = (
    "AI Image is part of an art session. Start or join one, then open it again."
)
READY_MESSAGE = (
    "Krita is ready with AI Image Generation, and a local image backend is "
    "running. Make a new image, or edit one you already have."
)
MANAGED_BACKEND_MESSAGE = (
    "Krita is ready with AI Image Generation. WebJam sees no local backend "
    "running yet; Krita's own plugin will start or ask for one."
)
RESULTS_ARE_YOURS_MESSAGE = (
    "Anything you make is a file on this computer that you own. Nothing is "
    "uploaded, and nothing reaches the room until you put it on the shared "
    "canvas yourself."
)
MADE_MESSAGE = (
    "Krita opened a new canvas. Use Settings, Dockers, AI Image Generation to "
    "describe what you want."
)
EDITING_MESSAGE = (
    "Krita opened your image. Use AI Image Generation to fill, extend, or "
    "remove part of it."
)


class AiImageState(str, Enum):
    """What this computer can honestly say about its AI image stack."""

    #: Not in a session. The action exists, but only inside a room.
    NOT_IN_A_ROOM = "not_in_a_room"
    NEEDS_KRITA = "needs_krita"
    NEEDS_PLUGIN = "needs_plugin"
    #: Krita and the plugin are installed and a loopback backend answered.
    READY = "ready"
    #: Krita and the plugin are installed; the backend is the plugin's to
    #: manage. Both ready states allow Make and Edit.
    READY_MANAGED_BACKEND = "ready_managed_backend"


_READY_STATES = frozenset(
    {AiImageState.READY, AiImageState.READY_MANAGED_BACKEND}
)

_STATE_MESSAGES: dict[AiImageState, str] = {
    AiImageState.NOT_IN_A_ROOM: NOT_IN_A_ROOM_MESSAGE,
    AiImageState.NEEDS_KRITA: INSTALL_KRITA_MESSAGE,
    AiImageState.NEEDS_PLUGIN: INSTALL_PLUGIN_MESSAGE,
    AiImageState.READY: READY_MESSAGE,
    AiImageState.READY_MANAGED_BACKEND: MANAGED_BACKEND_MESSAGE,
}


@dataclass(frozen=True, slots=True)
class AiImageAvailability:
    """What a probe of this computer's image stack found.

    ``backend_url`` is only ever a loopback address; :mod:`core.krita_ai`
    refuses anything else before a probe is built.
    """

    krita_found: bool = False
    plugin_found: bool = False
    backend_url: str = ""


@runtime_checkable
class AiImageStudio(Protocol):
    """The seam a real Krita launcher and test fakes both satisfy."""

    def probe(self) -> AiImageAvailability:
        """Report what is installed and whether a local backend answered."""

    def open_new_image(self) -> None:
        """Open Krita on a fresh canvas for generation."""

    def open_image(self, image: LocalImage) -> None:
        """Open Krita on one image the artist already owns."""


@dataclass(frozen=True, slots=True)
class AiImageSnapshot:
    """Local AI image truth, safe to render.

    There is no room-wide counterpart to this type, on purpose. Nothing about
    one artist's generator is any of the room's business.
    """

    state: AiImageState = AiImageState.NOT_IN_A_ROOM
    message: str = NOT_IN_A_ROOM_MESSAGE
    backend_label: str = ""
    activity: str = ""
    error: str = ""

    @property
    def can_generate(self) -> bool:
        return self.state in _READY_STATES

    @property
    def needs_install(self) -> bool:
        return self.state in {
            AiImageState.NEEDS_KRITA,
            AiImageState.NEEDS_PLUGIN,
        }


class AiImageController:
    """Offer Make and Edit only while both the room and the stack are real.

    The controller is local to one computer. It takes no role, publishes
    nothing, and holds no prompt: Krita owns the generation, and the file it
    writes belongs to the artist.
    """

    def __init__(
        self,
        studio: AiImageStudio,
        *,
        in_room: Callable[[], bool],
        on_change: Callable[[AiImageSnapshot], None] | None = None,
    ) -> None:
        self._studio = studio
        self._in_room = in_room
        self._on_change = on_change
        self._lock = threading.RLock()
        self._activity = ""
        self._error = ""

    # -- reads ---------------------------------------------------------

    @property
    def snapshot(self) -> AiImageSnapshot:
        with self._lock:
            return self._snapshot_locked()

    def _availability(self) -> AiImageAvailability:
        try:
            found = self._studio.probe()
        except AiImageError:
            raise
        except Exception:  # noqa: BLE001 - a probe must never break the room
            return AiImageAvailability()
        return found if isinstance(found, AiImageAvailability) else AiImageAvailability()

    def _snapshot_locked(self) -> AiImageSnapshot:
        if not self._in_room():
            # Reported before probing, so a person outside a room is told the
            # one thing that is actually stopping them.
            return AiImageSnapshot(
                state=AiImageState.NOT_IN_A_ROOM,
                message=NOT_IN_A_ROOM_MESSAGE,
            )
        found = self._availability()
        if not found.krita_found:
            state = AiImageState.NEEDS_KRITA
        elif not found.plugin_found:
            state = AiImageState.NEEDS_PLUGIN
        elif found.backend_url:
            state = AiImageState.READY
        else:
            state = AiImageState.READY_MANAGED_BACKEND
        return AiImageSnapshot(
            state=state,
            message=self._error or _STATE_MESSAGES[state],
            backend_label=found.backend_url if state is AiImageState.READY else "",
            activity=self._activity,
            error=self._error,
        )

    def _notify(self, snapshot: AiImageSnapshot) -> AiImageSnapshot:
        if self._on_change is not None:
            self._on_change(snapshot)
        return snapshot

    def _require_ready(self) -> None:
        snapshot = self._snapshot_locked()
        if snapshot.state is AiImageState.NOT_IN_A_ROOM:
            raise AiImageError(NOT_IN_A_ROOM_MESSAGE)
        if snapshot.state is AiImageState.NEEDS_KRITA:
            raise AiImageUnavailableError(INSTALL_KRITA_MESSAGE)
        if snapshot.state is AiImageState.NEEDS_PLUGIN:
            raise AiImageUnavailableError(INSTALL_PLUGIN_MESSAGE)

    # -- the two verbs -------------------------------------------------

    def make(self) -> AiImageSnapshot:
        """Open a fresh canvas so the artist can describe a new image."""

        with self._lock:
            self._require_ready()
            self._error = ""
            try:
                self._studio.open_new_image()
            except AiImageError:
                raise
            except Exception as exc:
                raise AiImageError(
                    "WebJam couldn't open Krita on this computer."
                ) from exc
            self._activity = MADE_MESSAGE
            return self._notify(self._snapshot_locked())

    def edit(self, path: object) -> AiImageSnapshot:
        """Open one image the artist already owns, for an AI edit."""

        with self._lock:
            self._require_ready()
            self._error = ""
            image = load_local_image(path)
            try:
                self._studio.open_image(image)
            except AiImageError:
                raise
            except Exception as exc:
                raise AiImageError(
                    "WebJam couldn't open Krita on this computer."
                ) from exc
            self._activity = f"{EDITING_MESSAGE} ({image.display_name})"
            return self._notify(self._snapshot_locked())

    def clear_activity(self) -> AiImageSnapshot:
        with self._lock:
            self._activity = ""
            self._error = ""
            return self._notify(self._snapshot_locked())


__all__ = [
    "EDITING_MESSAGE",
    "MADE_MESSAGE",
    "MANAGED_BACKEND_MESSAGE",
    "NOT_IN_A_ROOM_MESSAGE",
    "READY_MESSAGE",
    "RESULTS_ARE_YOURS_MESSAGE",
    "AiImageAvailability",
    "AiImageController",
    "AiImageSnapshot",
    "AiImageState",
    "AiImageStudio",
]
