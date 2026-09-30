"""Exercise the packaged workspace probe in a fresh source Qt interpreter."""
from __future__ import annotations

import json

from services.session_workspace_packaged_smoke import SUCCESS_MARKER, run_session_workspace_smoke


def main() -> int:
    print(json.dumps(run_session_workspace_smoke(), sort_keys=True))
    print(SUCCESS_MARKER)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
