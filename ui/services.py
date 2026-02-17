from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable


class RetryService:
    @staticmethod
    def retry_action(action: Callable[[], Any], attempts: int = 3, base_delay: float = 0.4) -> Any:
        """Retry an action on exception; callable should raise on failure."""
        last_exc: Exception | None = None
        for attempt in range(attempts):
            try:
                return action()
            except Exception as exc:
                last_exc = exc
                if attempt < attempts - 1:
                    time.sleep(base_delay * (attempt + 1))
        if last_exc is not None:
            raise last_exc
        return None


class MetricsService:
    METRIC_KEYS = [
        "metric_setup_wizard_opened",
        "metric_setup_wizard_completed",
        "metric_guided_tour_opened",
        "metric_guided_tour_completed",
        "metric_guided_tour_cancelled",
        "metric_jamulus_launch_attempt",
        "metric_jamulus_launch_success",
        "metric_jamulus_launch_failed",
        "metric_webex_open_attempt",
        "metric_webex_open_success",
        "metric_webex_open_failed",
        "metric_diagnostics_panel_opened",
        "metric_audio_diagnostics_opened",
        "metric_save_mix_attempt",
        "metric_save_mix_success",
        "metric_save_mix_failed",
        "metric_load_mix_attempt",
        "metric_load_mix_success",
        "metric_load_mix_failed",
    ]

    def __init__(self, repository: Any):
        self.repository = repository

    def increment(self, key: str) -> int:
        return self.repository.increment_setting(key)

    def collect(self) -> dict[str, str]:
        values: dict[str, str] = {}
        for key in self.METRIC_KEYS:
            values[key] = self.repository.get_setting(key, "0") or "0"
        return values

    def reset_with_prefix(self, prefix: str = "metric_") -> None:
        for key in self.repository.list_settings().keys():
            if key.startswith(prefix):
                self.repository.delete_setting(key)

    def export_snapshot(
        self,
        home_dir: Path,
        jamulus_state: str,
        webex_state: str,
        latency_ms: float | None,
        server: str,
        webex_url: str,
        audio_diagnostics: dict[str, Any],
    ) -> Path:
        timestamp = datetime.utcnow()
        snapshot = {
            "created_at": timestamp.isoformat() + "Z",
            "jamulus_state": jamulus_state,
            "webex_state": webex_state,
            "latency_ms": latency_ms,
            "server": server,
            "webex_url": webex_url,
            "audio_diagnostics": audio_diagnostics,
            "usage_metrics": self.collect(),
        }
        out_path = home_dir / f"webjam_diagnostics_{timestamp.strftime('%Y%m%d_%H%M%S')}.json"
        out_path.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
        return out_path
