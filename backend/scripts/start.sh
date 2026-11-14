#!/usr/bin/env bash
# Start the API, loading Earth Engine credentials from a file instead of the shell.
#
#   backend/scripts/start.sh              # uses ~/.config/sage/ee.env
#   SAGE_ENV_FILE=/path/to.env PORT=8001 backend/scripts/start.sh
#
# The env file is a plain `KEY=value` list, kept outside the repository because it
# names the service-account key. Missing file is not an error: the Earth Engine
# endpoints then report 503, by design.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$here"

env_file="${SAGE_ENV_FILE:-${XDG_CONFIG_HOME:-$HOME/.config}/sage/ee.env}"
if [ -f "$env_file" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$env_file"
  set +a
  echo "credentials: $env_file (project ${SAGE_EE_PROJECT:-unset})"
else
  echo "credentials: no $env_file — Earth Engine will report 503"
fi

if [ -x "$here/.venv/bin/python" ]; then
  python="$here/.venv/bin/python"
else
  python="${PYTHON:-python3}"
fi

exec "$python" -m uvicorn app.main:app --host "${HOST:-127.0.0.1}" --port "${PORT:-8000}" "$@"
