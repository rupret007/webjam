"""Fresh process entry for actual recorder and portable-workspace journeys."""
import json
import faulthandler
from pathlib import Path
import sys

from services.workspace_portability_smoke import (
    SUCCESS_MARKER, prepare_portability, resume_portability, run_workspace_portability_smoke,
    check_portable_launch,
)
from tests.support.controlled_recording_journey import record_completed_takes


if __name__ == "__main__":
    faulthandler.enable()
    faulthandler.dump_traceback_later(30)
    phase = sys.argv[1]
    if phase == "frozen":
        result = run_workspace_portability_smoke()
    else:
        root = Path(sys.argv[2]).resolve()
        if phase == "prepare":
            result = prepare_portability(root, record_takes=record_completed_takes)
        elif phase == "resume":
            result = resume_portability(root)
            result.update(check_portable_launch(root))
        else:
            raise ValueError("Unknown portable-workspace phase")
    print(json.dumps(result, sort_keys=True))
    print(SUCCESS_MARKER)
    faulthandler.cancel_dump_traceback_later()
