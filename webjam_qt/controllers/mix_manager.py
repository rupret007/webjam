"""Mix save/load/restore — persists per-channel fader, mute, solo, and pan.

Owns the bit of mixer state that survives an app restart:

* ``~/.webjam_mix.json`` — serialized participant mix (fader/mute/solo/pan)

Two interactive paths (``save`` / ``load``) flash user-facing messages on
success and on each failure mode (missing file, corrupted JSON, OS error,
generic).  A third best-effort path (``auto_restore``) is used when Jamulus
first connects: silent on missing file, debug-logged on any error, no flash.

Atomic writes (via ``core.file_io.atomic_write_text``) prevent half-written
files from corrupting state on crash.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Callable, Optional

from core.file_io import atomic_write_text

_MIX_FILE = ".webjam_mix.json"


class MixManager:
    """Loads and saves the per-channel mix for a single window.

    ``flash_callback`` matches the signature of ``ConductorWindow.flash_message``
    — ``(text: str, ms: int)``.  The two interactive entry points always pass
    an explicit duration so the callback contract is uniform; ``auto_restore``
    never flashes.
    """

    def __init__(
        self,
        jamulus_controller,
        flash_callback: Callable[[str, int], None],
        logger: Optional[logging.Logger] = None,
        metrics=None,  # Optional MetricsService — increments mix_corruption_recovered
    ) -> None:
        self._jamulus = jamulus_controller
        self._flash = flash_callback
        # The fallback stays inside the ``webjam`` namespace so take titles
        # and mix paths never reach an unredacted root-logger handler.
        self._log = logger or logging.getLogger("webjam.qt.mix_manager")
        self._metrics = metrics

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def save(self) -> bool:
        """Serialize current mixer state to ~/.webjam_mix.json.

        Returns True on a successful write, False if the write failed.  The
        caller uses this to decide whether to clear its dirty flag \u2014 clearing
        it after a failed save would disarm the shutdown auto-save safety net.
        """
        mix_path = Path.home() / _MIX_FILE
        try:
            payload = self._jamulus.serialize_mix()
            atomic_write_text(mix_path, json.dumps(payload, indent=2))
            self._log.info("Mix saved to %s", mix_path)
            self._flash("Mix saved  \u00b7  Ctrl+O to restore", 4000)
            return True
        except OSError:
            self._log.exception("Failed to save mix to %s", mix_path)
            self._flash(
                "Couldn't save mix. Check folder permissions and disk space.",
                6000,
            )
            return False
        except Exception:  # noqa: BLE001
            self._log.exception("Failed to save mix")
            self._flash(
                "Couldn't save mix. Check folder permissions and disk space.",
                6000,
            )
            return False

    def load(self) -> bool:
        """Load mixer state from ~/.webjam_mix.json and apply to Jamulus.

        Returns True if mix was loaded, False if the file was missing or
        an error occurred.  Always flashes a status message either way.
        """
        mix_path = Path.home() / _MIX_FILE
        if not mix_path.exists():
            self._flash("No saved mix found  \u00b7  Ctrl+S to save one", 4000)
            return False
        try:
            payload = json.loads(mix_path.read_text())
            self._jamulus.apply_mix_data(payload)
            self._log.info("Mix loaded from %s", mix_path)
            self._flash("Mix loaded", 4000)
            return True
        except json.JSONDecodeError as exc:
            self._log.exception("Mix file is corrupted")
            self._flash(
                f"Mix file is corrupted ({exc.msg}). Save a fresh one with Ctrl+S.",
                6000,
            )
            if self._metrics is not None:
                try:
                    self._metrics.increment("metric_mix_corruption_recovered")
                except Exception:  # noqa: BLE001
                    self._log.debug("metric increment failed", exc_info=True)
            return False
        except OSError:
            self._log.exception("Could not read mix file")
            self._flash(
                "Couldn't read mix file. Check file access and try again.",
                6000,
            )
            return False
        except Exception:  # noqa: BLE001
            self._log.exception("Failed to load mix")
            self._flash(
                "Couldn't load mix. Save a Support Bundle if this repeats.",
                6000,
            )
            return False

    def save_to(self, path: Path) -> bool:
        """Serialize current mixer state to ``path`` (atomic, indented JSON).

        Like :meth:`save` but writes to an explicit path instead of the
        default ``~/.webjam_mix.json`` slot — used by the "Save Mix As..."
        dialog so users can keep multiple named mixes (one per song,
        one per band-mate setup, etc.).  Returns True on success, False on
        failure.
        """
        try:
            payload = self._jamulus.serialize_mix()
            atomic_write_text(path, json.dumps(payload, indent=2))
            self._log.info("Mix saved to %s", path)
            self._flash(f"Mix saved to {path.name}", 4000)
            return True
        except OSError:
            self._log.exception("Failed to save mix to %s", path)
            self._flash(
                "Couldn't save mix. Check folder permissions and disk space.",
                6000,
            )
            return False
        except Exception:  # noqa: BLE001
            self._log.exception("Failed to save mix to %s", path)
            self._flash(
                "Couldn't save mix. Check folder permissions and disk space.",
                6000,
            )
            return False

    def load_from(self, path: Path) -> bool:
        """Load mixer state from ``path`` and apply to Jamulus.

        Like :meth:`load` but reads from an explicit path instead of the
        default ``~/.webjam_mix.json``.  Returns True if the mix was
        applied, False if the file was missing or an error occurred.
        Always flashes a status message either way.
        """
        if not path.exists():
            self._flash(
                f"No saved mix found at {path.name}  \u00b7  Ctrl+Shift+S to save one",
                4000,
            )
            return False
        try:
            payload = json.loads(path.read_text())
            self._jamulus.apply_mix_data(payload)
            self._log.info("Mix loaded from %s", path)
            self._flash(f"Mix loaded from {path.name}", 4000)
            return True
        except json.JSONDecodeError as exc:
            self._log.exception("Mix file is corrupted: %s", path)
            self._flash(
                f"Mix file is corrupted ({exc.msg}). Pick another file or save a fresh one.",
                6000,
            )
            if self._metrics is not None:
                try:
                    self._metrics.increment("metric_mix_corruption_recovered")
                except Exception:  # noqa: BLE001
                    self._log.debug("metric increment failed", exc_info=True)
            return False
        except OSError:
            self._log.exception("Could not read mix file %s", path)
            self._flash(
                "Couldn't read mix file. Check file access and try again.",
                6000,
            )
            return False
        except Exception:  # noqa: BLE001
            self._log.exception("Failed to load mix from %s", path)
            self._flash(
                "Couldn't load mix. Save a Support Bundle if this repeats.",
                6000,
            )
            return False

    def auto_restore(self) -> None:
        """Auto-apply ~/.webjam_mix.json when Jamulus first connects.

        Best-effort and silent: returns early if no file exists, debug-logged
        on any error, and never flashes a user-facing message — the caller
        decides whether to surface that "your mix is back" hint.
        """
        mix_path = Path.home() / _MIX_FILE
        if not mix_path.exists():
            return
        try:
            payload = json.loads(mix_path.read_text())
            self._jamulus.apply_mix_data(payload)
            self._log.info("Restored saved mix from %s", mix_path)
        except Exception:  # noqa: BLE001
            self._log.debug("Failed to restore saved mix from %s", mix_path, exc_info=True)
