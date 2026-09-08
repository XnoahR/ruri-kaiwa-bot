#!/usr/bin/env bash
# Jalanin Ruri pakai venv-nya sendiri, dari mana pun kamu berada.
set -euo pipefail
cd "$(dirname "$0")"
exec .venv/bin/python -m ruri "$@"
