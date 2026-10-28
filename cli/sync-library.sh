#!/usr/bin/env bash
# High-Speed Athenea Library & Storage Synchronization Scanner Runner
set -e

export PYTHONUNBUFFERED=1
export PYTHONPATH="/opt/athenea:${PYTHONPATH}"
cd /opt/athenea

PYTHON_BIN="/opt/athenea/venv/bin/python"
if [ ! -x "$PYTHON_BIN" ]; then
    PYTHON_BIN="python3"
fi

exec "$PYTHON_BIN" -u -m cli.sync_library "$@"
