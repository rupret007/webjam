"""Native build fetches may retry networking, never source or integrity failures."""

import contextlib
import io
import subprocess
import tempfile
import unittest
from pathlib import Path

from tools.fetch_native_go_modules import fetch_modules, transient_fetch_failure

DNS = 'go: example.org/lib@v1.0.0: Get "https://proxy.invalid/lib.zip": dial tcp: lookup proxy.invalid: i/o timeout'


def result(code=0, stderr=""):
    return subprocess.CompletedProcess([], code, stdout="", stderr=stderr)


class NativeGoFetchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "transport"
        self.root.mkdir()
        (self.root / "go.mod").write_text("module example.org/app\n")
        (self.root / "go.sum").write_text("reviewed module sums\n")
        self.calls = []
        self.delays = []
        self.output = io.StringIO()

    def fetch(self, *answers):
        remaining = iter(answers)

        def runner(args, **kwargs):
            self.calls.append(args)
            self.assertEqual(kwargs["cwd"], self.root)
            self.assertEqual(kwargs["timeout"], 90)
            self.assertNotIn("env", kwargs)
            answer = next(remaining)
            if isinstance(answer, Exception):
                raise answer
            return answer() if callable(answer) else answer

        with contextlib.redirect_stdout(self.output):
            return fetch_modules(self.root, runner=runner, sleep=self.delays.append)

    def test_success_requires_cache_verification(self):
        self.assertTrue(self.fetch(result(), result()))
        self.assertEqual(self.calls, [["go", "mod", "download"], ["go", "mod", "verify"]])
        self.assertEqual(self.delays, [])

    def test_dns_failure_recovers_without_changing_proxy_or_locks(self):
        before = [(self.root / n).read_bytes() for n in ("go.mod", "go.sum")]
        self.assertTrue(self.fetch(result(1, DNS), result(), result()))
        self.assertEqual(self.delays, [2])
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(before, [(self.root / n).read_bytes() for n in ("go.mod", "go.sum")])

    def test_transient_allowance_is_bounded(self):
        self.assertFalse(self.fetch(result(1, DNS), result(1, DNS), result(1, DNS)))
        self.assertEqual(self.delays, [2, 5])
        self.assertEqual(len(self.calls), 3)

    def test_checksum_failure_is_fatal_even_with_network_error(self):
        self.assertFalse(self.fetch(result(1, DNS + "\nSECURITY ERROR: checksum mismatch")))
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.delays, [])

    def test_mixed_unknown_failure_is_not_retried(self):
        self.assertFalse(self.fetch(result(1, DNS + "\nunknown revision v9.0.0")))
        self.assertEqual(len(self.calls), 1)

    def test_empty_failure_is_not_retried(self):
        self.assertFalse(self.fetch(result(1)))
        self.assertEqual(len(self.calls), 1)

    def test_cache_integrity_failure_is_not_retried(self):
        self.assertFalse(self.fetch(result(), result(1, "modified module cache")))
        self.assertEqual(self.calls[-1], ["go", "mod", "verify"])
        self.assertEqual(self.delays, [])

    def test_changed_sum_stops_even_if_download_succeeds(self):
        def changed():
            (self.root / "go.sum").write_text("unexpected replacement\n")
            return result()

        self.assertFalse(self.fetch(changed))
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.delays, [])
        self.assertEqual((self.root / "go.sum").read_text(), "unexpected replacement\n")

    def test_changed_mod_stops_before_transient_retry(self):
        def changed():
            (self.root / "go.mod").write_text("module unexpected.org/app\n")
            return result(1, DNS)

        self.assertFalse(self.fetch(changed))
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.delays, [])

    def test_lock_change_during_backoff_stops_before_second_process(self):
        def runner(args, **kwargs):
            self.calls.append(args)
            return result(1, DNS)

        def changed_during_backoff(delay):
            (self.root / "go.sum").write_text("changed while waiting\n")

        with contextlib.redirect_stdout(self.output):
            passed = fetch_modules(self.root, runner=runner, sleep=changed_during_backoff)
        self.assertFalse(passed)
        self.assertEqual(len(self.calls), 1)

    def test_timeout_stops_without_starting_another_fetch(self):
        self.assertFalse(self.fetch(subprocess.TimeoutExpired(["go"], 90)))
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.delays, [])

    def test_missing_tool_stops_without_retry(self):
        self.assertFalse(self.fetch(FileNotFoundError("go unavailable")))
        self.assertEqual(len(self.calls), 1)

    def test_missing_lock_stops_before_go(self):
        (self.root / "go.sum").unlink()
        self.assertFalse(self.fetch())
        self.assertEqual(self.calls, [])

    def test_symlink_lock_is_refused_before_go(self):
        original = self.root / "reviewed.sum"
        (self.root / "go.sum").rename(original)
        try:
            (self.root / "go.sum").symlink_to(original)
        except OSError:
            self.skipTest("symlink creation unavailable")
        self.assertFalse(self.fetch())
        self.assertEqual(self.calls, [])

    def test_proxy_credentials_are_not_logged(self):
        error = DNS.replace("proxy.invalid", "secret-user:secret-token@proxy.invalid")
        self.assertFalse(self.fetch(result(1, error), result(1, error), result(1, error)))
        self.assertNotIn("secret-token", self.output.getvalue())
        self.assertNotIn("https://", self.output.getvalue())

    def test_only_known_completed_network_errors_are_transient(self):
        self.assertTrue(transient_fetch_failure("go: downloading example.org/lib v1.0.0\n" + DNS))
        self.assertTrue(transient_fetch_failure("Get file: 504 Gateway Timeout"))
        self.assertFalse(transient_fetch_failure("401 Unauthorized"))
        self.assertFalse(transient_fetch_failure("403 Forbidden"))
        self.assertFalse(transient_fetch_failure("404 Not Found"))
        self.assertFalse(transient_fetch_failure("TLS certificate verification failed"))


if __name__ == "__main__":
    unittest.main()
