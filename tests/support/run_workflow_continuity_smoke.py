"""Fresh-process runner also usable when auditing the frozen proof locally."""
import json

from services.workflow_continuity_packaged_smoke import (
    SUCCESS_MARKER, run_workflow_continuity_smoke,
)

if __name__ == "__main__":
    print(json.dumps(run_workflow_continuity_smoke(), sort_keys=True))
    print(SUCCESS_MARKER)
