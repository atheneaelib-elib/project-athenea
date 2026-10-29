#!/usr/bin/env bash
# Athenea Admin-Only CLI Book Deletion Wrapper
set -e

if [ "$(id -u)" -ne 0 ] && [ "$(id -un)" != "athenea" ] && [ "$(id -un)" != "books" ]; then
    echo "Error: delete-books can only be executed by root or the athenea service user." >&2
    exit 1
fi

export PYTHONUNBUFFERED=1
export PYTHONPATH="/opt/athenea:${PYTHONPATH}"
cd /opt/athenea

PYTHON_BIN="/opt/athenea/venv/bin/python"
if [ ! -x "$PYTHON_BIN" ]; then
    PYTHON_BIN="python3"
fi

exec "$PYTHON_BIN" -u -m cli.delete_books "$@"
