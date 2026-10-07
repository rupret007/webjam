"""Explicit Help navigation through the existing owners; no status authority."""

from __future__ import annotations


class WorkflowHelpCoordinator:
    def __init__(self, controller) -> None:
        self._c = controller
        controller.window.workflow_help_requested.connect(self.navigate)

    def navigate(self, route: str) -> None:
        controller = self._c
        if route not in {"library", "rehearsal", "art", "studio"}:
            return
        audio = getattr(controller, "audio", None)
        if (any(bool(getattr(controller, flag, False)) for flag in (
                "_shutdown", "_shutdown_in_progress", "_shutdown_cleanup_pending",
                "_workspace_transition_pending"))
                or bool(getattr(audio, "stopping", False))
                or bool(getattr(audio, "cleanup_retry_required", False))
                or not controller.window.isVisible()):
            return
        # Read the profile and mode now, not when Help was opened. Restoring a
        # workspace or completing teardown can change them while Help is up.
        profile = controller.creator_profile
        offline = bool(getattr(controller, "_offline_reference_studio", False))
        if route == "studio":
            allowed = profile.capabilities.local_multitrack if offline else profile.capabilities.take_review
            if allowed:
                controller._on_rail_view_changed("takes")
            return
        if offline:
            controller.window.flash_message(
                "Return to launch to open Session library. Your local project stays separate.", ms=6000,
            )
            return
        if route == "library":
            controller.session_library.show()
        elif ((route == "rehearsal" and profile.key == "music")
                or (route == "art" and profile.key == "art")):
            controller.session_library.show(tab="plan")
