#!/bin/bash

# Explicit advanced entry point.  The shared installer owns validation,
# installation, rollback, and the narrowly scoped quarantine removal.

set -euo pipefail

readonly SCRIPT_DIR="$(cd -P -- "$(dirname -- "$0")" && pwd)"
exec "$SCRIPT_DIR/Install WebJam.command" --remove-quarantine
