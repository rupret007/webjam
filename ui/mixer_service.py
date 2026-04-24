import tkinter as tk
import json
import logging
from pathlib import Path
from typing import Optional, Dict, Any, Callable, List

from tkinter import messagebox as tk_messagebox

from jamulus_controller import JamulusController
from storage.repository import WebJamRepository
from ui.auth_controller import AuthController, UserContext
# NOTE: tk_messagebox.showerror was moved from webjam_app_enhanced.py
from core.creative_modes import get_mode_by_key_or_default
from core.settings import AppSettings

LOGGER = logging.getLogger(__name__)

# Fallback mix file path — superseded by AppSettings.mix_file when MixerService
# is constructed with a settings object.
_DEFAULT_MIX_FILE = Path.home() / ".webjam_mix.json"
LOCAL_PARTICIPANT_NAME = "You (Local)"
RECONNECT_MAX_ATTEMPTS = 5 # These might be app-level, but are tightly coupled here
RECONNECT_BASE_DELAY_SECONDS = 1.5
RECONNECT_MAX_DELAY_SECONDS = 45.0

class MixerService:
    def __init__(
        self,
        app_root: tk.Tk,
        repository: WebJamRepository,
        auth_controller: AuthController,
        jamulus_controller: JamulusController,
        metrics_service: Any, # MetricsService type
        get_current_user: Callable[[], Optional[UserContext]],
        get_mode_key: Callable[[], str],
        refresh_readiness: Callable[[], None],
        set_status_banner: Callable[[str, str], None],
        mixer_channels: Dict[int, Any], # EnhancedMixerChannel type
        app_root_exists_check: Callable[[], bool],
        settings: Optional[AppSettings] = None,
    ):
        self.app_root = app_root
        self.repository = repository
        self.auth_controller = auth_controller
        self.jamulus_controller = jamulus_controller
        self.metrics_service = metrics_service
        self.get_current_user = get_current_user
        self.get_mode_key = get_mode_key
        self.refresh_readiness = refresh_readiness
        self.set_status_banner = set_status_banner
        self.mixer_channels = mixer_channels
        self.app_root_exists_check = app_root_exists_check

        # Resolve mix file path from settings (preferred) or fall back to default.
        if settings is not None:
            self._mix_file = Path(settings.mix_file)
        else:
            self._mix_file = _DEFAULT_MIX_FILE

        self._pending_mix_restore_payload: Optional[Dict[str, Any]] = None
        self._pending_mix_restore_source: Optional[str] = None
        
    def _default_mix_user(self) -> Optional[str]:
        user = self.get_current_user()
        if user is None:
            return None
        username = str(getattr(user, "username", "") or "").strip()
        return username or None

    def _load_mix_payload_from_file(self, mix_file: Path = _DEFAULT_MIX_FILE) -> Optional[Dict[str, Any]]:
        try:
            with Path(mix_file).open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            LOGGER.warning("Could not read mix payload from %s: %s", mix_file, exc)
            return None
        if not isinstance(payload, dict):
            LOGGER.warning("Mix payload from %s was not a JSON object.", mix_file)
            return None
        return payload

    def _queue_mix_restore(self, payload: Dict[str, Any], source_label: str) -> None:
        self._pending_mix_restore_payload = dict(payload)
        self._pending_mix_restore_source = str(source_label)
        self._attempt_pending_mix_restore()

    def _attempt_pending_mix_restore(self) -> None:
        payload = getattr(self, "_pending_mix_restore_payload", None)
        if not isinstance(payload, dict):
            return
        applied_channels = self.jamulus_controller.apply_mix_data(payload)
        if applied_channels is None:
            LOGGER.warning(
                "Discarding invalid pending mix restore payload from %s.",
                self._pending_mix_restore_source or "unknown source",
            )
            self._pending_mix_restore_payload = None
            self._pending_mix_restore_source = None
            return
        if applied_channels <= 0:
            return
        self._pending_mix_restore_payload = None
        restored_from = self._pending_mix_restore_source or "saved mix"
        self._pending_mix_restore_source = None
        self._refresh_mixer_controls_from_participants()
        self.refresh_readiness()
        try:
            self.set_status_banner(f"Restored mix from {restored_from}", color="#00cc66")
        except (tk.TclError, RuntimeError, AttributeError):
            pass

    def _restore_startup_mix_default(self) -> None:
        if not self._mix_file.exists():
            return
        payload = self._load_mix_payload_from_file(self._mix_file)
        if payload is None:
            return
        self._queue_mix_restore(payload, str(self._mix_file))

    def _restore_signed_in_mix_default(self) -> None:
        username = self._default_mix_user()
        if not username:
            return
        saved_mix = self.repository.get_user_mix_default(username)
        if saved_mix is None:
            return
        payload = saved_mix.get("payload")
        if not isinstance(payload, dict):
            LOGGER.warning("Saved user mix for '%s' was not a mapping payload.", username)
            return
        self._queue_mix_restore(payload, f"{username} profile")

    def _available_profile_entries(self) -> List[Dict[str, str]]:
        current_mode_profiles = self.repository.list_mix_profiles(self.get_mode_key())
        seen_names = {profile["profile_name"] for profile in current_mode_profiles}
        all_profiles = self.repository.list_mix_profiles()
        other_profiles = [profile for profile in all_profiles if profile["profile_name"] not in seen_names]
        return current_mode_profiles + other_profiles

    def _profile_prompt_text(self, action: str, profiles: List[Dict[str, str]]) -> str:
        lines = [f"{action} listening profile", ""]
        if not profiles:
            lines.append("No profiles saved yet.")
            return "\n".join(lines)
        lines.append("Available profiles:")
        for profile in profiles[:10]:
            mode_label = get_mode_by_key_or_default(profile.get("mode_key", "")).label
            lines.append(f"- {profile['profile_name']} ({mode_label})")
        if len(profiles) > 10:
            lines.append(f"...and {len(profiles) - 10} more")
        lines.append("")
        lines.append("Enter the profile name exactly as shown.")
        return "\n".join(lines)

    @staticmethod
    def _resolve_profile_name(name: str, profiles: List[Dict[str, str]]) -> Optional[str]:
        normalized = str(name or "").strip()
        if not normalized:
            return None
        by_casefold = {profile["profile_name"].casefold(): profile["profile_name"] for profile in profiles}
        return by_casefold.get(normalized.casefold())

    def _refresh_mixer_controls_from_participants(self) -> None:
        for channel_id, channel in list(self.mixer_channels.items()):
            try:
                p = channel.participant
                channel.fader.set(p.fader_level)
                channel.pan_slider.set(p.pan)
                channel.update_button_states()
            except (KeyError, tk.TclError, AttributeError):
                continue

    def _select_mixer_channel(self, channel_id: Optional[int]) -> None:
        if channel_id is not None and channel_id not in self.mixer_channels:
            channel_id = None
        # self.selected_channel_id = channel_id # This should be handled by the main app or passed back
        for current_id, channel in list(self.mixer_channels.items()):
            try:
                channel.set_selected(current_id == channel_id)
            except (tk.TclError, AttributeError):
                continue
        # Need to signal back to app what is selected.
        # For now, this is a direct call on the `EnhancedMixerChannel` which in turn calls `on_select`
        # which will update the app's `selected_channel_id`.
        if channel_id is not None and channel_id in self.mixer_channels:
             # Trigger the on_select callback on the channel to update app's selected_channel_id
            self.mixer_channels[channel_id].on_select(channel_id)


    def _selected_mixer_channel(self) -> Optional[Any]: # EnhancedMixerChannel type
        # This will need to get the selected_channel_id from the main app
        # For now, it will require `self.selected_channel_id` to be passed or accessed via app
        # Revisit once the main app no longer directly manages `selected_channel_id`
        current_selected_id = getattr(self.app_root, "selected_channel_id", None)
        if current_selected_id is None:
            return None
        return self.mixer_channels.get(current_selected_id)

    def save_mix(self) -> None:
        self.metrics_service.increment("metric_save_mix_attempt")
        if not self.auth_controller.authorize(
            self.get_current_user(),
            "save_mix",
            require_sign_in=False,
            allow_anonymous=True,
            parent=self.app_root,
        ):
            return
        participants = self.jamulus_controller.get_participants()
        if not participants:
            tk_messagebox.showwarning(
                "No Participants",
                "Connect or add participants before saving mix settings.",
                parent=self.app_root,
            )
            return
        try:
            username = self._default_mix_user()
            audit_actor = self.get_current_user().username if self.get_current_user() else "anonymous"
            if username:
                mix_payload = self.jamulus_controller.serialize_mix()
                self.repository.save_user_mix_default(username, mix_payload)
                audit_detail = f"user:{username}"
                success_message = (
                    f"Mix settings saved to your WebJam profile for {username}.\n\n"
                    "This default mix restores automatically when you sign in again."
                )
            else:
                self.jamulus_controller.save_mix(str(self._mix_file))
                audit_detail = str(self._mix_file)
                success_message = (
                    f"Mix settings saved locally to:\n{self._mix_file}\n\n"
                    "This default mix restores automatically the next time WebJam starts."
                )
            self._pending_mix_restore_payload = None
            self._pending_mix_restore_source = None
            self.metrics_service.increment("metric_save_mix_success")
            self.repository.add_audit("save_mix", audit_actor, audit_detail)
            tk_messagebox.showinfo("Saved", success_message, parent=self.app_root)
        except Exception as e:
            LOGGER.exception("save_mix failed: %s", e)
            self.metrics_service.increment("metric_save_mix_failed")
            tk_messagebox.showerror(
                self.app_root,
                "Save Mix Failed",
                what_failed=f"Could not save the default mix ({e}).",
                likely_cause="Repository write failed, the mix payload was invalid, or the local mix file path is unavailable.",
                next_action="Verify the current profile/path in diagnostics, then retry save.",
                retry_callback=self.save_mix,
            )

    def save_listening_profile(self) -> None:
        if not self.auth_controller.authorize(
            self.get_current_user(),
            "save_mix",
            require_sign_in=False,
            allow_anonymous=True,
            parent=self.app_root,
        ):
            return
        participants = self.jamulus_controller.get_participants()
        if not participants:
            tk_messagebox.showwarning("No Participants", "Connect or add participants before saving a listening profile.", parent=self.app_root)
            return

        suggested_name = f"{get_mode_by_key_or_default(self.get_mode_key()).label} Profile"
        profile_name = tk.simpledialog.askstring(
            "Save Listening Profile",
            "Enter a name for this listening profile:",
            parent=self.app_root,
            initialvalue=suggested_name,
        )
        if profile_name is None:
            return
        normalized_name = profile_name.strip()
        if not normalized_name:
            tk_messagebox.showwarning("Invalid Name", "Profile name cannot be empty.", parent=self.app_root)
            return

        existing = self.repository.get_mix_profile(normalized_name)
        if existing is not None:
            overwrite = tk_messagebox.askokcancel(
                "Overwrite Listening Profile",
                f"A listening profile named '{existing['profile_name']}' already exists.\n\nOverwrite it?",
                parent=self.app_root,
            )
            if not overwrite:
                return

        try:
            self.repository.save_mix_profile(
                normalized_name,
                self.get_mode_key(),
                self.jamulus_controller.serialize_mix(),
            )
            self.metrics_service.increment("metric_listening_profile_save_success")
            self.repository.add_audit(
                "save_mix_profile",
                self.get_current_user().username if self.get_current_user() else "anonymous",
                normalized_name,
            )
            tk_messagebox.showinfo(
                "Listening Profile Saved",
                f"Saved listening profile:\n{normalized_name}",
                parent=self.app_root,
            )
        except Exception as exc:
            LOGGER.exception("save_listening_profile failed: %s", exc)
            self.metrics_service.increment("metric_listening_profile_save_failed")
            tk_messagebox.showerror(
                "Save Failed",
                f"Could not save listening profile:\n{exc}",
                parent=self.app_root,
            )

    def load_mix(self) -> None:
        self.metrics_service.increment("metric_load_mix_attempt")
        if not self.auth_controller.authorize(
            self.get_current_user(),
            "load_mix",
            require_sign_in=False,
            allow_anonymous=True,
            parent=self.app_root,
        ):
            return
        try:
            saved_mix = self._saved_mix_payload_for_load()
            if saved_mix is None:
                if self._mix_file.exists() and self._load_mix_payload_from_file(self._mix_file) is None:
                    self.metrics_service.increment("metric_load_mix_failed")
                    tk_messagebox.showerror(
                        self.app_root,
                        "Load Mix Failed",
                        what_failed="The saved mix file could not be parsed or was missing the expected participant payload.",
                        likely_cause="The file is corrupted, incomplete, or from an unsupported format.",
                        next_action="Save a fresh mix preset after confirming participants are connected.",
                        retry_callback=self.load_mix,
                    )
                    return
                tk_messagebox.showwarning("No Settings", "No saved mix settings found.", parent=self.app_root)
                return

            mix_payload, mix_source = saved_mix
            applied_channels = self.jamulus_controller.apply_mix_data(mix_payload)
            if applied_channels is None:
                self.metrics_service.increment("metric_load_mix_failed")
                tk_messagebox.showerror(
                    self.app_root,
                    "Load Mix Failed",
                    what_failed="The saved mix payload could not be parsed or was missing the expected participant data.",
                    likely_cause="The saved mix is corrupted, incomplete, or from an unsupported format.",
                    next_action="Save a fresh mix preset after confirming participants are connected.",
                    retry_callback=self.load_mix,
                )
                return
            if applied_channels == 0:
                self.metrics_service.increment("metric_load_mix_failed")
                tk_messagebox.showwarning(
                    "No Matching Participants",
                    "Saved mix was read, but none of the current participants matched the saved channels.\n\n"
                    "Reconnect the expected participants and try again.",
                    parent=self.app_root,
                )
                return
            self._pending_mix_restore_payload = None
            self._pending_mix_restore_source = None
            self._refresh_mixer_controls_from_participants()
            self.refresh_readiness()
            tk_messagebox.showinfo("Loaded", f"Mix settings loaded from:\n{mix_source}", parent=self.app_root)
            self.metrics_service.increment("metric_load_mix_success")
        except Exception as e:
            LOGGER.exception("load_mix failed: %s", e)
            self.metrics_service.increment("metric_load_mix_failed")
            tk_messagebox.showerror(
                self.app_root,
                "Load Mix Failed",
                what_failed=f"Could not load the saved mix ({e}).",
                likely_cause="Corrupted or incompatible saved mix data.",
                next_action="Save a fresh mix preset after confirming participants are connected.",
                retry_callback=self.load_mix,
            )

    def load_listening_profile(self) -> None:
        if not self.auth_controller.authorize(
            self.get_current_user(),
            "load_mix",
            require_sign_in=False,
            allow_anonymous=True,
            parent=self.app_root,
        ):
            return
        profiles = self._available_profile_entries()
        if not profiles:
            tk_messagebox.showinfo("No Profiles", "No listening profiles are saved yet.", parent=self.app_root)
            return

        profile_name = tk.simpledialog.askstring(
            "Load Listening Profile",
            self._profile_prompt_text("Load", profiles),
            parent=self.app_root,
        )
        if profile_name is None:
            return
        resolved_name = self._resolve_profile_name(profile_name, profiles)
        if resolved_name is None:
            tk_messagebox.showwarning("Profile Not Found", "Enter one of the saved listening profile names.", parent=self.app_root)
            return

        profile = self.repository.get_mix_profile(resolved_name)
        if profile is None:
            self.metrics_service.increment("metric_listening_profile_load_failed")
            tk_messagebox.showerror("Load Failed", f"Listening profile '{resolved_name}' could not be read.", parent=self.app_root)
            return

        try:
            applied_channels = self.jamulus_controller.apply_mix_data(profile["payload"])
            if applied_channels is None:
                self.metrics_service.increment("metric_listening_profile_load_failed")
                tk_messagebox.showerror(
                    "Load Failed",
                    f"Listening profile '{resolved_name}' is invalid or corrupted.",
                    parent=self.app_root,
                )
                return
            if applied_channels == 0:
                self.metrics_service.increment("metric_listening_profile_load_failed")
                tk_messagebox.showwarning(
                    "No Matching Participants",
                    f"Listening profile '{resolved_name}' did not match any current participants.",
                    parent=self.app_root,
                )
                return
            self._refresh_mixer_controls_from_participants()
            self.refresh_readiness()
            self.metrics_service.increment("metric_listening_profile_load_success")
            self.repository.add_audit(
                "load_mix_profile",
                self.get_current_user().username if self.get_current_user() else "anonymous",
                resolved_name,
            )
            tk_messagebox.showinfo(
                "Listening Profile Loaded",
                f"Loaded listening profile:\n{resolved_name}",
                parent=self.app_root,
            )
        except Exception as exc:
            LOGGER.exception("load_listening_profile failed: %s", exc)
            self.metrics_service.increment("metric_listening_profile_load_failed")
            tk_messagebox.showerror("Load Failed", f"Could not load listening profile:\n{exc}", parent=self.app_root)

    def delete_listening_profile(self) -> None:
        if not self.auth_controller.authorize(
            self.get_current_user(),
            "save_mix",
            require_sign_in=False,
            allow_anonymous=True,
            parent=self.app_root,
        ):
            return
        profiles = self._available_profile_entries()
        if not profiles:
            tk_messagebox.showinfo("No Profiles", "No listening profiles are saved yet.", parent=self.app_root)
            return

        profile_name = tk.simpledialog.askstring(
            "Delete Listening Profile",
            self._profile_prompt_text("Delete", profiles),
            parent=self.app_root,
        )

        if profile_name is None:
            return
        resolved_name = self._resolve_profile_name(profile_name, profiles)
        if resolved_name is None:
            tk_messagebox.showwarning("Profile Not Found", "Enter one of the saved listening profile names.", parent=self.app_root)
            return

        confirmed = tk_messagebox.askokcancel(
            "Delete Listening Profile",
            f"Delete listening profile '{resolved_name}'?",
            parent=self.app_root,
        )
        if not confirmed:
            return

        deleted = self.repository.delete_mix_profile(resolved_name)
        if not deleted:
            self.metrics_service.increment("metric_listening_profile_delete_failed")
            tk_messagebox.showerror("Delete Failed", f"Listening profile '{resolved_name}' was not found.", parent=self.app_root)
            return

        self.metrics_service.increment("metric_listening_profile_delete_success")
        self.repository.add_audit(
            "delete_mix_profile",
            self.get_current_user().username if self.get_current_user() else "anonymous",
            resolved_name,
        )
        tk_messagebox.showinfo("Listening Profile Deleted", f"Deleted listening profile:\n{resolved_name}", parent=self.app_root)

    def reset_all_faders(self) -> None:
        if not self.auth_controller.authorize(
            self.get_current_user(),
            "bulk_reset",
            require_sign_in=False,
            parent=self.app_root,
        ):
            return
        if not tk_messagebox.askokcancel("Confirm", "Reset all faders to default?", parent=self.app_root):
            return
        for participant in self.jamulus_controller.get_participants():
            self.jamulus_controller.set_fader_level(participant.channel_id, 100)
            if participant.channel_id in self.mixer_channels:
                self.mixer_channels[participant.channel_id].fader.set(100)
        self.repository.add_audit("bulk_reset", self.get_current_user().username if self.get_current_user() else "anonymous", "reset_all_faders")
    
    def unmute_all(self) -> None:
        if not self.auth_controller.authorize(
            self.get_current_user(),
            "bulk_mute",
            require_sign_in=False,
            parent=self.app_root,
        ):
            return
        if not tk_messagebox.askokcancel("Confirm", "Unmute all channels?", parent=self.app_root):
            return
        for participant in self.jamulus_controller.get_participants():
            self.jamulus_controller.set_mute(participant.channel_id, False)
            if participant.channel_id in self.mixer_channels:
                self.mixer_channels[participant.channel_id].update_button_states()
        self.repository.add_audit("bulk_mute", self.get_current_user().username if self.get_current_user() else "anonymous", "unmute_all")
    
    def center_all_pans(self) -> None:
        if not self.auth_controller.authorize(
            self.get_current_user(),
            "bulk_reset",
            require_sign_in=False,
            parent=self.app_root,
        ):
            return
        if not tk_messagebox.askokcancel("Confirm", "Center all pan controls?", parent=self.app_root):
            return
        for participant in self.jamulus_controller.get_participants():
            self.jamulus_controller.set_pan(participant.channel_id, 50)
            if participant.channel_id in self.mixer_channels:
                self.mixer_channels[participant.channel_id].pan_slider.set(50)
        self.repository.add_audit("bulk_reset", self.get_current_user().username if self.get_current_user() else "anonymous", "center_all_pans")
