from __future__ import annotations

import unittest

from admin.policy import PolicyEngine, UserContext


class TestPolicyEngine(unittest.TestCase):
    def setUp(self):
        self.policy = PolicyEngine()

    def test_admin_allows_all_defined_actions(self):
        admin = UserContext(username="admin", role="admin")
        for action in ("change_endpoint", "bulk_mute", "bulk_reset", "view_diagnostics", "save_mix", "load_mix"):
            self.assertTrue(self.policy.allows(admin, action), f"admin should allow {action}")

    def test_operator_allows_subset(self):
        operator = UserContext(username="op", role="operator")
        self.assertTrue(self.policy.allows(operator, "bulk_mute"))
        self.assertTrue(self.policy.allows(operator, "save_mix"))
        self.assertFalse(self.policy.allows(operator, "change_endpoint"))

    def test_performer_limited(self):
        performer = UserContext(username="user", role="performer")
        self.assertTrue(self.policy.allows(performer, "save_mix"))
        self.assertTrue(self.policy.allows(performer, "load_mix"))
        self.assertFalse(self.policy.allows(performer, "bulk_reset"))
        self.assertFalse(self.policy.allows(performer, "change_endpoint"))

    def test_unknown_role_denies_all(self):
        unknown = UserContext(username="x", role="guest")
        for action in ("change_endpoint", "bulk_mute", "bulk_reset", "view_diagnostics", "save_mix", "load_mix"):
            self.assertFalse(self.policy.allows(unknown, action), f"unknown role should deny {action}")

    def test_unknown_action_denied_for_admin(self):
        admin = UserContext(username="admin", role="admin")
        self.assertFalse(self.policy.allows(admin, "nonexistent_action"))

    def test_empty_role_denies(self):
        user = UserContext(username="u", role="")
        self.assertFalse(self.policy.allows(user, "save_mix"))

    def test_empty_action_denied(self):
        admin = UserContext(username="admin", role="admin")
        self.assertFalse(self.policy.allows(admin, ""))


if __name__ == "__main__":
    unittest.main()
