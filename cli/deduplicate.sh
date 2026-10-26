#!/usr/bin/env bash
# EBook Library Deduplication Tool Runner
set -e

export PYTHONUNBUFFERED=1
export PYTHONPATH="/opt/bookserver/api:${PYTHONPATH}"
cd /opt/bookserver/api

PYTHON_BIN="/opt/bookserver/api/venv/bin/python"
if [ ! -x "$PYTHON_BIN" ]; then
    PYTHON_BIN="python3"
fi

exec "$PYTHON_BIN" -u -m app.cli.deduplicate "$@"
