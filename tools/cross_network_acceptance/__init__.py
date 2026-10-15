"""CI acceptance harness for same-LAN vs two-NAT Host/Join.

This package is invoked by the optional ``Cross-network acceptance`` workflow
and by ``python -m tools.cross_network_acceptance``. It is not collected as
``tests/test_*.py``, so it cannot weaken or skip the required master suite.
"""

from __future__ import annotations

import os
import sys

SCHEMA = "webjam.cross-network-acceptance.v1"
CONDUCTOR_KIND = "webjam.cross-network-acceptance"

_product_root = str(os.environ.get("WEBJAM_PRODUCT_ROOT", "") or "").strip()
if _product_root:
    sys.path.insert(0, _product_root)
