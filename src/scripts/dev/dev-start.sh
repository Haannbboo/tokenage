#!/usr/bin/env bash
# Start an isolated API server + frontend dev server for worktree development.
# Each invocation gets its own ephemeral DB (copied from main), free ports, and
# independent processes. Multiple worktrees can run concurrently.
#
# Usage: ./src/scripts/dev/dev-start.sh
# Stop:  ./src/scripts/dev/dev-stop.sh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
STATE_FILE="${ROOT_DIR}/.dev-env.json"
MAIN_DB="${HOME}/.tokenage/usage.db"
VENV_DIR="${ROOT_DIR}/.venv"
PYTHON="${VENV_DIR}/bin/python"
REQS_STAMP="${VENV_DIR}/.requirements.sha256"

# --- Bootstrap venv ---
if ! command -v uv >/dev/null 2>&1; then
  echo "==> Installing uv..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="${HOME}/.local/bin:${PATH}"
fi

if [[ ! -x "${PYTHON}" ]]; then
  echo "==> Creating venv..."
  uv venv --python 3.13 "${VENV_DIR}"
fi

CURRENT_HASH="$(shasum -a 256 "${ROOT_DIR}/src/pyproject.toml" | awk '{print $1}')"
SAVED_HASH="$(cat "${REQS_STAMP}" 2>/dev/null || true)"
if [[ "${CURRENT_HASH}" != "${SAVED_HASH}" ]]; then
  echo "==> Installing Python dependencies..."
  uv pip install --python "${PYTHON}" -r "${ROOT_DIR}/src/pyproject.toml" --extra dev
  echo "${CURRENT_HASH}" > "${REQS_STAMP}"
fi

# --- Bootstrap frontend deps ---
if [[ ! -d "${ROOT_DIR}/frontend/node_modules" ]]; then
  echo "==> Installing frontend dependencies..."
  (cd "${ROOT_DIR}/frontend" && npm install)
fi

# --- Check if already running ---
if [[ -f "${STATE_FILE}" ]]; then
  api_pid=$(python3 -c "import json; print(json.load(open('${STATE_FILE}'))['api_pid'])" 2>/dev/null || echo "")
  if [[ -n "${api_pid}" ]] && kill -0 "${api_pid}" 2>/dev/null; then
    api_port=$(python3 -c "import json; print(json.load(open('${STATE_FILE}'))['api_port'])")
    vite_port=$(python3 -c "import json; print(json.load(open('${STATE_FILE}'))['vite_port'])")
    echo "==> Already running."
    echo "    API:     http://127.0.0.1:${api_port}"
    echo "    Frontend: http://localhost:${vite_port}"
    exit 0
  fi
  # Stale state file — clean up
  rm -f "${STATE_FILE}"
fi

# --- Find free ports ---
find_port() {
  python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()'
}

API_PORT="$(find_port)"
VITE_PORT="$(find_port)"

# --- Create ephemeral work dir and copy DB ---
WORK_DIR="$(mktemp -d -t tokenage-dev-)"
DB_PATH="${WORK_DIR}/usage.db"
DB_URL="sqlite:///${DB_PATH}"

if [[ -f "${MAIN_DB}" ]]; then
  cp "${MAIN_DB}" "${DB_PATH}"
  # WAL mode keeps recent commits in the -wal file until a checkpoint.
  for suffix in -wal -shm; do
    [[ -f "${MAIN_DB}${suffix}" ]] && cp "${MAIN_DB}${suffix}" "${DB_PATH}${suffix}"
  done
  echo "==> Copied ${MAIN_DB} -> ${DB_PATH}"
else
  echo "==> No main DB found at ${MAIN_DB}, starting with empty database"
fi

# --- Migrate schema (in case worktree has newer schema) ---
TOKENAGE_DB_URL="${DB_URL}" PYTHONPATH="${ROOT_DIR}" "${PYTHON}" -m src.cli migrate

# --- Cleanup on exit ---
cleanup() {
  echo "==> Shutting down..."
  [[ -n "${API_PID:-}" ]] && kill "${API_PID}" 2>/dev/null || true
  [[ -n "${VITE_PID:-}" ]] && kill "${VITE_PID}" 2>/dev/null || true
  wait 2>/dev/null || true
  rm -rf "${WORK_DIR}"
  rm -f "${STATE_FILE}"
  echo "==> Cleaned up."
}
trap cleanup EXIT INT TERM

# --- Start API server ---
TOKENAGE_QUEUE_URL="sqlite:///${WORK_DIR}/state.db" TOKENAGE_DB_URL="${DB_URL}" "${PYTHON}" -m uvicorn src.api:app \
  --host 127.0.0.1 --port "${API_PORT}" --log-level warning --reload &
API_PID=$!

# Wait for API readiness
echo "==> Waiting for API on port ${API_PORT}..."
for _ in $(seq 30); do
  if curl -sf "http://127.0.0.1:${API_PORT}/config" >/dev/null 2>&1; then
    break
  fi
  sleep 0.3
done

if ! kill -0 "${API_PID}" 2>/dev/null; then
  echo "==> API server failed to start."
  rm -rf "${WORK_DIR}"
  exit 1
fi

# --- Start frontend dev server ---
cd "${ROOT_DIR}/frontend"
TOKENAGE_API_URL="http://127.0.0.1:${API_PORT}" \
  npx vite --port "${VITE_PORT}" --strict-port &
VITE_PID=$!

# --- Persist state ---
python3 -c "
import json
json.dump({
    'api_port': ${API_PORT},
    'vite_port': ${VITE_PORT},
    'api_pid': ${API_PID},
    'vite_pid': ${VITE_PID},
    'db_path': '${DB_PATH}',
    'work_dir': '${WORK_DIR}',
}, open('${STATE_FILE}', 'w'), indent=2)
"

echo ""
echo "==> Dev environment ready."
echo "    API:      http://127.0.0.1:${API_PORT}"
echo "    Frontend: http://localhost:${VITE_PORT}"
echo "    DB copy:  ${DB_PATH}"
echo ""
echo "    Stop with: ./src/scripts/dev/dev-stop.sh"
echo ""

# Wait for background processes (trap handles cleanup)
wait
