"""Optional Cross-network acceptance harness. Not part of tests/test_*.py."""

from __future__ import annotations

import os
import sys

SCHEMA = "webjam.cross-network-acceptance.v1"
CONDUCTOR_KIND = "webjam.cross-network-acceptance"

_product_root = str(os.environ.get("WEBJAM_PRODUCT_ROOT", "") or "").strip()
if _product_root:
    sys.path.insert(0, _product_root)
