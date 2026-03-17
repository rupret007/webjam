from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from ui.views.diagnostics_panel import show_diagnostics_panel


class _DummyWidget:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs

    def pack(self, *args, **kwargs):
        return None

    def configure(self, *args, **kwargs):
        return None

    config = configure

    def bind(self, *args, **kwargs):
        return None

    def destroy(self):
        return None

    def protocol(self, *args, **kwargs):
        return None

    def title(self, *args, **kwargs):
        return None

    def geometry(self, *args, **kwargs):
        return None

    def transient(self, *args, **kwargs):
        return None

    def grab_set(self):
        return None

    def insert(self, *args, **kwargs):
        return None


class _DummyPanel(_DummyWidget):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.grab_count = 0

    def grab_set(self):
        self.grab_count += 1


class _ButtonFactory:
    def __init__(self):
        self.buttons = []

    def __call__(self, *args, **kwargs):
        button = _DummyWidget(*args, **kwargs)
        button.text = kwargs.get("text", "")
        button.command = kwargs.get("command")
        self.buttons.append(button)
        return button


class TestDiagnosticsPanelEdge(unittest.TestCase):
    def test_run_setup_button_does_not_regrab_diagnostics_panel(self):
        created_panels: list[_DummyPanel] = []

        def _make_panel(*args, **kwargs):
            panel = _DummyPanel(*args, **kwargs)
            created_panels.append(panel)
            return panel

        panel_factory = MagicMock(side_effect=_make_panel)
        button_factory = _ButtonFactory()
        run_setup = MagicMock()

        with patch("ui.views.diagnostics_panel.tk.Toplevel", panel_factory), patch(
            "ui.views.diagnostics_panel.tk.Frame", side_effect=lambda *args, **kwargs: _DummyWidget(*args, **kwargs)
        ), patch(
            "ui.views.diagnostics_panel.tk.Text", side_effect=lambda *args, **kwargs: _DummyWidget(*args, **kwargs)
        ), patch(
            "ui.views.diagnostics_panel.tk.Button", side_effect=button_factory
        ):
            show_diagnostics_panel(
                root=object(),
                jamulus_path="C:/Jamulus.exe",
                jamulus_server="jam.example.com",
                jamulus_port="22124",
                host_ok=True,
                host_detail="ok",
                webex_url="https://example.webex.com/meet/test",
                webex_last_error="",
                audio_diagnostics={"backend": "wasapi"},
                on_run_setup=run_setup,
                on_open_help=MagicMock(),
                on_export_snapshot=MagicMock(),
                on_export_bundle=MagicMock(),
                on_reset_metrics=MagicMock(),
            )

        panel = created_panels[0]
        run_setup_button = next(button for button in button_factory.buttons if button.text == "Run Setup Wizard")

        self.assertEqual(panel.grab_count, 1)

        run_setup_button.command()

        run_setup.assert_called_once_with()
        self.assertEqual(panel.grab_count, 1)


if __name__ == "__main__":
    unittest.main()
