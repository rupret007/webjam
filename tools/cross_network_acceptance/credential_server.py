"""Run the existing local coturn mint with a proof-run-only user identity."""

from __future__ import annotations

import os

from deploy.relay import turn_credentials
from tools.cross_network_acceptance.turn import PROOF_USER


def main() -> int:
    proof_user = os.environ.get("WEBJAM_TEST_TURN_USER", "")
    if not PROOF_USER.fullmatch(proof_user):
        return 2
    # This process belongs to one isolated harness run. Keep the production
    # HTTP/token/rate/quota checks, while preventing a paid-provider fallback.
    turn_credentials.USER = proof_user
    turn_credentials.CF_TOKEN = ""
    turn_credentials.CF_URL = ""
    return turn_credentials.main()


if __name__ == "__main__":
    raise SystemExit(main())
