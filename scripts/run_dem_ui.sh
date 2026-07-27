#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Prefer the repository's documented virtual environment even when the caller
# forgot to activate it in this terminal.
if [[ -x "$repo_root/.venv/bin/ohqbuild" ]]; then
  exec "$repo_root/.venv/bin/ohqbuild" ui "$@"
fi

if command -v ohqbuild >/dev/null 2>&1; then
  exec ohqbuild ui "$@"
fi

cd "$repo_root"
python_command="${PYTHON:-python3}"
exec "$python_command" -m ohqbuilder.cli ui "$@"
