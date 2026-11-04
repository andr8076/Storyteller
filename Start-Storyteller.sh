#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# Open the interactive terminal control panel. No background service.
exec konsole --workdir "$HERE" -e python3 "$HERE/storyteller_ui.py"
