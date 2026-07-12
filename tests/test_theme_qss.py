"""Theme stylesheet integrity (webjam_qt/theme)."""
from __future__ import annotations

import unittest

from webjam_qt.theme import load_stylesheet


class TestStylesheet(unittest.TestCase):
    def setUp(self):
        self.qss = load_stylesheet()

    def test_all_tokens_substituted(self):
        """A typo'd ${TOKEN} would reach Qt as literal text and silently
        disable every rule after it."""
        self.assertNotIn("${", self.qss)

    def test_settings_wizard_widgets_are_styled(self):
        # The SetupWizard QWizard previously rendered native chrome; keep it
        # inside the dark theme.
        for selector in ("QWizard", "QSpinBox", "QLabel#WizardError"):
            self.assertIn(selector, self.qss)


if __name__ == "__main__":
    unittest.main()
