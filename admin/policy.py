from __future__ import annotations

from dataclasses import dataclass


@dataclass
class UserContext:
    username: str
    role: str


class PolicyEngine:
    """
    Local policy guard.
    Uses simple role rules and can be replaced with pycasbin later.
    """

    _ROLE_ACTIONS = {
        "admin": {
            "change_endpoint",
            "bulk_mute",
            "bulk_reset",
            "view_diagnostics",
            "save_mix",
            "load_mix",
        },
        "operator": {"bulk_mute", "bulk_reset", "view_diagnostics", "save_mix", "load_mix"},
        "performer": {"save_mix", "load_mix"},
    }

    def allows(self, user: UserContext, action: str) -> bool:
        actions = self._ROLE_ACTIONS.get(user.role, set())
        return action in actions

