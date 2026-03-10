from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from ui.services import MetricsService


class _RepoStub:
    def __init__(self) -> None:
        self._settings: dict[str, str] = {}

    def increment_setting(self, key: str, amount: int = 1) -> int:
        current = int(self._settings.get(key, "0"))
        current += amount
        self._settings[key] = str(current)
        return current

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        return self._settings.get(key, default)

    def list_settings(self) -> dict[str, str]:
        return dict(self._settings)

    def delete_setting(self, key: str) -> None:
        self._settings.pop(key, None)


class TestMetricsServiceExportEdge(unittest.TestCase):
    def setUp(self) -> None:
        self.service = MetricsService(_RepoStub())

    def test_export_snapshot_creates_missing_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            out_dir = Path(temp_dir) / "missing" / "nested"
            out_path = self.service.export_snapshot(
                home_dir=out_dir,
                jamulus_state="Connected",
                webex_state="Opened",
                latency_ms=12.3,
                server="localhost:22124",
                webex_url="https://webex.example.com/meet/test",
                audio_diagnostics={"backend": "synthetic"},
            )
            self.assertTrue(out_dir.exists())
            self.assertTrue(out_path.exists())
            data = json.loads(out_path.read_text(encoding="utf-8"))
            self.assertEqual(data["jamulus_state"], "Connected")

    def test_export_snapshot_rejects_file_path(self) -> None:
        fd, temp_path = tempfile.mkstemp()
        os.close(fd)
        try:
            with self.assertRaises(NotADirectoryError):
                self.service.export_snapshot(
                    home_dir=Path(temp_path),
                    jamulus_state="Connected",
                    webex_state="Opened",
                    latency_ms=None,
                    server="localhost:22124",
                    webex_url="https://webex.example.com/meet/test",
                    audio_diagnostics={},
                )
        finally:
            os.remove(temp_path)


if __name__ == "__main__":
    unittest.main()
