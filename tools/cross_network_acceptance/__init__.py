"""Proof-only two-NAT Host/Join. Non-success is FAIL."""

import os
import sys

SCHEMA = "webjam.cross-network-acceptance.v2"
CONDUCTOR_KIND = "webjam.cross-network-acceptance"
PROOF_PORT = 41000
CASE_TIMEOUT_S = 90.0
SETUP_TRIES = 3

_product_root = (os.environ.get("WEBJAM_PRODUCT_ROOT") or "").strip()
if _product_root:
    sys.path.insert(0, _product_root)


class HarnessError(RuntimeError):
    pass
