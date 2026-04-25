"""Live (as-you-type) validation hints in SetupWizard pages.

These tests cover the additive `_host_hint` and `_url_hint` labels that
appear/disappear via `textChanged` signals, complementing the existing
`validatePage` coverage in `test_qt_setup_wizard.py`.

Runs headlessly via QT_QPA_PLATFORM=offscreen (set automatically in CI).
Skipped when PySide6 is not importable.
"""
from __future__ import annotations

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from core.settings import AppSettings


def _pyside6_available() -> bool:
    try:
        import PySide6  # noqa: F401
        return True
    except ImportError:
        return False


def _qapp():
    """Return (or lazily create) the QApplication singleton."""
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv[:1])
    return app


skip_no_pyside6 = unittest.skipUnless(_pyside6_available(), "PySide6 not installed")


# ---------------------------------------------------------------------------
# _JamulusPage live host hint
# ---------------------------------------------------------------------------
@skip_no_pyside6
class TestJamulusLiveHint(unittest.TestCase):
    def setUp(self):
        _qapp()

    def test_jamulus_host_with_spaces_shows_hint(self):
        from webjam_qt.windows.setup_wizard import _JamulusPage
        page = _JamulusPage(AppSettings(jamulus_server=""))
        page._host.setText("my band.example.com")
        self.assertFalse(page._host_hint.isHidden())
        self.assertIn("spaces", page._host_hint.text())

    def test_jamulus_host_clean_hides_hint(self):
        from webjam_qt.windows.setup_wizard import _JamulusPage
        page = _JamulusPage(AppSettings(jamulus_server=""))
        # First trigger the hint, then clear it with a clean host.
        page._host.setText("bad host")
        self.assertFalse(page._host_hint.isHidden())
        page._host.setText("myband.example.com")
        self.assertTrue(page._host_hint.isHidden())

    def test_jamulus_host_empty_hides_hint(self):
        from webjam_qt.windows.setup_wizard import _JamulusPage
        page = _JamulusPage(AppSettings(jamulus_server=""))
        page._host.setText("bad host")
        self.assertFalse(page._host_hint.isHidden())
        page._host.setText("")
        self.assertTrue(page._host_hint.isHidden())


# ---------------------------------------------------------------------------
# _WebexPage live URL hint
# ---------------------------------------------------------------------------
@skip_no_pyside6
class TestWebexLiveHint(unittest.TestCase):
    def setUp(self):
        _qapp()

    def test_webex_url_no_scheme_with_dot_shows_will_prepend_hint(self):
        from webjam_qt.windows.setup_wizard import _WebexPage
        page = _WebexPage(AppSettings(webex_url=""))
        page._url.setText("myorg.webex.com/meet/bandroom")
        self.assertFalse(page._url_hint.isHidden())
        self.assertIn("auto-prepend", page._url_hint.text())

    def test_webex_url_localhost_shows_needs_domain_hint(self):
        from webjam_qt.windows.setup_wizard import _WebexPage
        page = _WebexPage(AppSettings(webex_url=""))
        page._url.setText("https://localhost")
        self.assertFalse(page._url_hint.isHidden())
        self.assertIn("domain", page._url_hint.text())

    def test_webex_url_clean_https_hides_hint(self):
        from webjam_qt.windows.setup_wizard import _WebexPage
        page = _WebexPage(AppSettings(webex_url=""))
        page._url.setText("https://myorg.webex.com/meet/bandroom")
        self.assertTrue(page._url_hint.isHidden())

    def test_webex_url_with_spaces_shows_hint(self):
        from webjam_qt.windows.setup_wizard import _WebexPage
        page = _WebexPage(AppSettings(webex_url=""))
        page._url.setText("https://my org.webex.com/meet/x")
        self.assertFalse(page._url_hint.isHidden())
        self.assertIn("spaces", page._url_hint.text())

    def test_webex_url_with_dot_dot_shows_hint(self):
        from webjam_qt.windows.setup_wizard import _WebexPage
        page = _WebexPage(AppSettings(webex_url=""))
        page._url.setText("https://myorg..webex.com/meet/x")
        self.assertFalse(page._url_hint.isHidden())
        self.assertIn("..", page._url_hint.text())

    def test_webex_url_empty_hides_hint(self):
        from webjam_qt.windows.setup_wizard import _WebexPage
        page = _WebexPage(AppSettings(webex_url=""))
        page._url.setText("https://localhost")
        self.assertFalse(page._url_hint.isHidden())
        page._url.setText("")
        self.assertTrue(page._url_hint.isHidden())


if __name__ == "__main__":
    unittest.main()
