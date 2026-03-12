from __future__ import annotations

import unittest

from webjam_app_enhanced import WebJamEnhancedApp


class TestReadyCheckSummaryEdge(unittest.TestCase):
    def test_summary_marks_ready_when_all_checks_pass(self):
        report = WebJamEnhancedApp._summarize_ready_check(
            check_results=[
                ("Jamulus executable", True, "ok"),
                ("Jamulus server reachability", True, "ok"),
            ],
            latency_ms=12.5,
            participant_count=2,
        )
        self.assertEqual(report["passed"], 2)
        self.assertEqual(report["total"], 2)
        self.assertEqual(report["failed"], [])
        self.assertIn("Ready", str(report["summary"]))
        self.assertEqual(report["participant_count"], 2)
        self.assertIn("Latency", str(report["latency_label"]))

    def test_summary_lists_failed_checks_when_not_ready(self):
        report = WebJamEnhancedApp._summarize_ready_check(
            check_results=[
                ("Jamulus executable", False, "missing"),
                ("Webex URL", True, "ok"),
                ("Audio diagnostics", False, "inactive"),
            ],
            latency_ms=None,
            participant_count=0,
        )
        self.assertEqual(report["passed"], 1)
        self.assertEqual(report["total"], 3)
        self.assertEqual(report["failed"], ["Jamulus executable", "Audio diagnostics"])
        self.assertIn("Not ready", str(report["summary"]))
        self.assertIn("unreachable", str(report["latency_label"]).lower())


if __name__ == "__main__":
    unittest.main()
