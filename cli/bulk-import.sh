#!/usr/bin/env bash
# High-Performance Ebook Library Bulk Importer & Converter Runner
set -e

export PYTHONPATH="/opt/bookserver/api:${PYTHONPATH}"
cd /opt/bookserver/api

PYTHON_BIN="/opt/bookserver/api/venv/bin/python"
if [ ! -x "$PYTHON_BIN" ]; then
    PYTHON_BIN="python3"
fi

exec "$PYTHON_BIN" -m app.cli.bulk_import "$@"
