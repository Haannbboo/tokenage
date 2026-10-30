#!/usr/bin/env bash
# Isolated functional checks; never reads an operator's config or database.
set -euo pipefail
# One metadata-only Claude OTLP usage record.
SMOKE_RECORD='{"resourceLogs":[{"resource":{"attributes":[{"key":"service.name","value":{"stringValue":"claude-code"}}]},"scopeLogs":[{"logRecords":[{"timeUnixNano":"1800000000000000000","attributes":[{"key":"event.name","value":{"stringValue":"claude_code.api_request"}},{"key":"model","value":{"stringValue":"docker-smoke-model"}},{"key":"input_tokens","value":{"intValue":"10"}},{"key":"output_tokens","value":{"intValue":"5"}},{"key":"status_code","value":{"intValue":"200"}}]}]}]}]}'
cd "$(dirname "$0")/.."
repo=$PWD
scratch=$(mktemp -d)
export TOKENAGE_SMOKE_IMAGE=${TOKENAGE_SMOKE_IMAGE:-tokenage:smoke}
export POSTGRES_PASSWORD=smoke-only
project="tokenage-smoke-$(basename "$scratch" | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9')"
compose=(docker compose -p "$project" --project-directory "$scratch" -f "$repo/docker/compose.yml" -f "$scratch/override.yml")
cleanup() {
    "${compose[@]}" --profile postgres --profile redis --profile caddy down -v --remove-orphans >/dev/null 2>&1 || true
    rm -rf "$scratch"
}
trap cleanup EXIT
cat > "$scratch/override.yml" <<'YAML'
services:
  tokenage:
    image: ${TOKENAGE_SMOKE_IMAGE}
    restart: "no"
    ports: !override ["127.0.0.1::4001"]
  second:
    extends:
      service: tokenage
    ports: !override ["127.0.0.1::4001"]
    volumes: !override
      - second-data:/data
      - type: bind
        source: ./config.yaml
        target: /data/config.yaml
        read_only: true
  caddy:
    ports: !override ["127.0.0.1::80"]
volumes:
  second-data:
YAML
write_config() {
    cat > "$scratch/config.yaml" <<'YAML'
pricing:
  auto_fetch: false
server:
  host: 127.0.0.1
  api_port: 4001
db:
  path: /data/usage.db
YAML
    chmod 644 "$scratch/config.yaml"
}
wait_ready() {
    local service=$1 port=$2
    endpoint="http://$("${compose[@]}" port "$service" "$port")"
    for _ in $(seq 90); do
        if curl --max-time 5 --fail --silent "$endpoint/version" >/dev/null; then return; fi
        sleep 1
    done
    echo "Timed out waiting for $service /version" >&2
    return 1
}
ingest_and_assert() {
    # Capture stdout, never print the token. The request body contains metadata only.
    "${compose[@]}" exec -T tokenage python -m src.cli token create \
        --email smoke@example.test --kind ingest --name docker-smoke > "$scratch/token"
    tail -n 1 "$scratch/token" > "$scratch/secret"
    printf 'x-tokenage-token: %s\n' "$(cat "$scratch/secret")" > "$scratch/headers"
    curl --max-time 10 --fail --silent --output /dev/null -H @"$scratch/headers" -H 'Content-Type: application/json' \
        --data-binary "$SMOKE_RECORD" "$endpoint/otlp/v1/logs"
    "${compose[@]}" exec -T tokenage python - <<'PY'
import time
from sqlalchemy import text
from src.database import get_engine
for _ in range(60):
    with get_engine().connect() as connection:
        count = connection.execute(text("SELECT COUNT(*) FROM usage WHERE model = 'docker-smoke-model' AND prompt_tokens = 10 AND completion_tokens = 5")).scalar_one()
    if count == 1:
        break
    time.sleep(1)
else:
    raise SystemExit("OTLP usage row did not land exactly once")
PY
    rm -f "$scratch/token" "$scratch/secret" "$scratch/headers"
}
assert_row() {
    "${compose[@]}" exec -T tokenage python - <<'PY'
from sqlalchemy import text
from src.database import get_engine
with get_engine().connect() as connection:
    assert connection.execute(text("SELECT COUNT(*) FROM usage WHERE model = 'docker-smoke-model'")).scalar_one() == 1
PY
}
write_config
cat >> "$scratch/config.yaml" <<'YAML'
  url: postgresql+psycopg://tokenage:smoke-only@postgres:5432/tokenage
YAML
"${compose[@]}" --profile postgres up -d --wait postgres
"${compose[@]}" --profile postgres up -d tokenage
wait_ready tokenage 4001
ingest_and_assert
"${compose[@]}" --profile postgres down -v
echo 'PASS: Postgres OTLP ingest'

write_config
"${compose[@]}" up -d tokenage
wait_ready tokenage 4001
ingest_and_assert
"${compose[@]}" up -d --force-recreate tokenage
wait_ready tokenage 4001
assert_row
"${compose[@]}" down -v
echo 'PASS: SQLite survives container recreation'

write_config
cat >> "$scratch/config.yaml" <<'YAML'
  url: postgresql+psycopg://tokenage:smoke-only@postgres:5432/tokenage
queue:
  url: redis://redis:6379/0
  prefix: "tokenage-smoke:"
YAML
python3 - "$scratch/config.yaml" <<'PY'
import sys
from pathlib import Path
path = Path(sys.argv[1])
config = path.read_text()
assert config.count("  api_port: 4001") == 1
config = config.replace("  api_port: 4001", "  base_url: https://smoke.example.test\n  api_port: 4001")
assert "  base_url: https://smoke.example.test\n  api_port: 4001" in config
path.write_text(config)
PY
mkdir "$scratch/caddy"
cp docker/caddy/Caddyfile.example "$scratch/caddy/Caddyfile"
# Static second address models an independently hosted node. Start sequentially
# below so the two entrypoints cannot race schema migrations.
chmod 644 "$scratch/caddy/Caddyfile"
"${compose[@]}" --profile postgres --profile redis --profile caddy up -d --wait postgres redis
"${compose[@]}" --profile postgres --profile redis --profile caddy up -d --wait tokenage
"${compose[@]}" --profile postgres --profile redis up -d --wait second
second_ip=$(docker inspect --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$("${compose[@]}" ps -q second)")
python3 - "$scratch/caddy/Caddyfile" <<'PYCONFIG'
import sys
from pathlib import Path
path = Path(sys.argv[1])
config = path.read_text()
active = "\n".join(line.split("#", 1)[0] for line in config.splitlines())
assert "lb_try_duration 5s" in active
assert "lb_retry_match expression false" in active, "Sent requests must never be replayed"
assert "keepalive off" in active, "Transport retries must not bypass the retry matcher"
assert "health_uri /version" in active and "health_interval 5s" in active
for directive in (
    "max_size 10MiB",
    "dial_timeout 1s",
    "response_header_timeout 60s",
    "lb_policy least_conn",
    "fail_duration 10s",
    "max_fails 1",
    "flush_interval -1",
):
    assert directive in active, f"Missing required directive: {directive}"
assert "trusted_proxies" not in active
PYCONFIG
"${compose[@]}" --profile caddy up -d caddy
wait_ready caddy 80
# Caddy must not become a tokenless local owner, even with spoofed loopback headers.
status=$(curl --max-time 10 --silent --output /dev/null --write-out '%{http_code}' \
    -H 'Host: localhost' -H 'X-Forwarded-For: 127.0.0.1' \
    -H 'X-Forwarded-Proto: https' -H 'X-Forwarded-Host: localhost' "$endpoint/usage")
[ "$status" = 401 ]
ingest_and_assert
echo 'PASS: Caddy + Redis + Postgres OTLP ingest and auth isolation'
# Keep a token for the body-limit and post-replacement POST probes.
"${compose[@]}" exec -T tokenage python -m src.cli token create \
    --email smoke@example.test --kind ingest --name docker-probes > "$scratch/token"
printf 'x-tokenage-token: %s\n' "$(tail -n 1 "$scratch/token")" > "$scratch/headers"
python3 - "$scratch/oversized.json" "$SMOKE_RECORD" <<'PY'
import sys
from pathlib import Path
Path(sys.argv[1]).write_bytes(b" " * (10 * 1024 * 1024) + sys.argv[2].encode())
PY
status=$(curl --max-time 30 --silent --output /dev/null --write-out '%{http_code}' \
    -H @"$scratch/headers" -H 'Content-Type: application/json' \
    --data-binary @"$scratch/oversized.json" "$endpoint/otlp/v1/logs")
[ "$status" = 413 ]
rm -f "$scratch/oversized.json"
echo 'PASS: Caddy rejects ingest bodies larger than 10 MiB'
# Installer origin is operator-configured, never taken from request headers.
curl --max-time 5 --fail --silent -H 'Host: other.example.test' "$endpoint/install.sh" |
    python3 -c "import sys; assert 'TOKENAGE_INSTALL_SERVER=https://smoke.example.test\n' in sys.stdin.read()"
"${compose[@]}" exec -T tokenage python - <<'PY'
import os
from pathlib import Path
from src.shared_state import get_shared_state
assert os.getuid() == 10001
assert not Path('/app/client').exists() and not Path('/app/plugins').exists()
assert 'redis' in type(get_shared_state()).__name__.lower()
PY
# With only tokenage configured, replacement must recover without a Caddy reload.
caddy_endpoint=$endpoint
"${compose[@]}" up -d --force-recreate tokenage
wait_ready tokenage 4001
# The first client probe through Caddy must be a POST, since a GET could hide a
# stale pooled connection by being replayed. Use a separate model for row counts.
curl --max-time 10 --fail --silent --output /dev/null -H @"$scratch/headers" -H 'Content-Type: application/json' \
    --data-binary "${SMOKE_RECORD/docker-smoke-model/docker-recreated-model}" "$caddy_endpoint/otlp/v1/logs"
"${compose[@]}" exec -T tokenage python - <<'PY'
import time
from sqlalchemy import text
from src.database import get_engine
for _ in range(60):
    with get_engine().connect() as connection:
        count = connection.execute(text("SELECT COUNT(*) FROM usage WHERE model = 'docker-recreated-model'")).scalar_one()
    if count == 1:
        break
    time.sleep(1)
else:
    raise SystemExit("Post-replacement ingest did not land exactly once")
PY
rm -f "$scratch/token" "$scratch/headers"
wait_ready caddy 80
echo 'PASS: Caddy ingests through the replaced upstream without a reload'
# Save by rename like an editor: a directory mount must expose the new inode.
python3 - "$scratch/caddy/Caddyfile" "$second_ip" <<'PYCONFIG'
import sys
from pathlib import Path
path = Path(sys.argv[1])
replacement = path.with_suffix(".new")
config = path.read_text()
assert config.count(":80 {") == 1
config = config.replace(":80 {", ':80 {\n\theader X-Smoke-Reload "new-inode"')
assert 'header X-Smoke-Reload "new-inode"' in config
assert config.count("# to 192.168.x.y:4001") == 1
config = config.replace("# to 192.168.x.y:4001", f"to {sys.argv[2]}:4001")
assert f"to {sys.argv[2]}:4001" in config and "# to 192.168.x.y:4001" not in config
replacement.write_text(config)
replacement.replace(path)
PYCONFIG
"${compose[@]}" exec -T caddy caddy validate --config /etc/caddy/Caddyfile
"${compose[@]}" exec -T caddy caddy reload --config /etc/caddy/Caddyfile
wait_ready caddy 80
for _ in $(seq 30); do
    if curl --max-time 5 --fail --silent --dump-header - --output /dev/null "$endpoint/version" | tr -d '\r' | grep -qi '^X-Smoke-Reload: new-inode$'; then break; fi
    sleep 1
done
curl --max-time 5 --fail --silent --dump-header - --output /dev/null "$endpoint/version" | tr -d '\r' | grep -qi '^X-Smoke-Reload: new-inode$'
assert_row
echo 'PASS: configured installer origin, Redis store, non-root image and Caddy reload after atomic config save'

# Force ordered selection and disable retries so successful requests can only
# bypass a stopped first node if background health checks removed it. No proxied
# requests run between stop and the assertion (passive checks cannot mask it).
cp "$scratch/caddy/Caddyfile" "$scratch/caddy/Caddyfile.failover"
python3 - "$scratch/caddy/Caddyfile" <<'PYCONFIG'
import sys
from pathlib import Path
path = Path(sys.argv[1])
replacement = path.with_suffix(".new")
config = path.read_text()
assert config.count("lb_policy least_conn") == 1
config = config.replace("lb_policy least_conn", "lb_policy first")
assert "lb_policy first" in config and "lb_policy least_conn" not in config
assert config.count("lb_try_duration 5s") == 1
config = config.replace("lb_try_duration 5s", "lb_try_duration 0")
assert "lb_try_duration 0" in config and "lb_try_duration 5s" not in config
replacement.write_text(config)
replacement.replace(path)
PYCONFIG
"${compose[@]}" exec -T caddy caddy validate --config /etc/caddy/Caddyfile
"${compose[@]}" exec -T caddy caddy reload --config /etc/caddy/Caddyfile
wait_ready caddy 80
"${compose[@]}" stop tokenage
# Allow one full 5s interval plus the 2s health timeout and scheduling margin.
sleep 10
for _ in $(seq 6); do
    curl --max-time 5 --fail --silent --output /dev/null "$endpoint/version"
done
echo 'PASS: active health checks bypass a stopped upstream with retries disabled'
"${compose[@]}" up -d --wait tokenage
wait_ready tokenage 4001

# Six sequential functional requests, never a load test. Disable active checks
# for this assertion so the first POST must actually try the stopped first node.
# The example's keepalive off ensures a fresh dial to the stopped node.
python3 - "$scratch/caddy/Caddyfile" <<'PYCONFIG'
import sys
from pathlib import Path
path = Path(sys.argv[1])
config = path.with_name("Caddyfile.failover").read_text()
assert config.count("lb_policy least_conn") == 1
config = config.replace("lb_policy least_conn", "lb_policy first")
assert "lb_policy first" in config and "lb_policy least_conn" not in config
assert any(line.strip().startswith("health_") for line in config.splitlines())
config = "\n".join(line for line in config.splitlines() if not line.strip().startswith("health_")) + "\n"
active = "\n".join(line.split("#", 1)[0] for line in config.splitlines())
assert "health_" not in active
replacement = path.with_suffix(".new")
replacement.write_text(config)
replacement.replace(path)
PYCONFIG
"${compose[@]}" exec -T caddy caddy validate --config /etc/caddy/Caddyfile
"${compose[@]}" exec -T caddy caddy reload --config /etc/caddy/Caddyfile
wait_ready caddy 80
"${compose[@]}" exec -T tokenage python -m src.cli token create \
    --email failover@example.test --kind ingest --name docker-failover > "$scratch/token"
printf 'x-tokenage-token: %s\n' "$(tail -n 1 "$scratch/token")" > "$scratch/headers"
"${compose[@]}" stop tokenage
for _ in $(seq 6); do
    curl --max-time 10 --fail --silent --output /dev/null -H @"$scratch/headers" -H 'Content-Type: application/json' \
        --data-binary "$SMOKE_RECORD" "$endpoint/otlp/v1/logs"
done
"${compose[@]}" exec -T second python - <<'PYASSERT'
import time
from sqlalchemy import text
from src.database import get_engine
for _ in range(60):
    with get_engine().connect() as connection:
        count = connection.execute(text("SELECT COUNT(*) FROM usage WHERE model = 'docker-smoke-model'")).scalar_one()
    if count == 7:
        break
    time.sleep(1)
else:
    raise SystemExit(f"Expected exactly seven accepted rows, got {count}")
# Confirm the queue drains and the final cardinality remains stable.
from src.shared_state import get_shared_state
for _ in range(30):
    if all(partition.depth == 0 for partition in get_shared_state().status()):
        break
    time.sleep(1)
else:
    raise SystemExit("Failover queue did not drain")
time.sleep(1)
with get_engine().connect() as connection:
    assert connection.execute(text("SELECT COUNT(*) FROM usage WHERE model = 'docker-smoke-model'")).scalar_one() == 7
PYASSERT
echo 'PASS: abrupt backend stop fails over POSTs and drains all accepted records'

# Caddy logs request headers and the URI on errors: with no upstream left, the
# ingest token and a one-time sign-in code must stay out of its logs.
"${compose[@]}" stop tokenage second
token=$(tail -n 1 "$scratch/token")
[ -n "$token" ] || { echo 'FAIL: no ingest token' >&2; exit 1; }
curl --max-time 10 --silent --output /dev/null -H @"$scratch/headers" --data-binary '{}' "$endpoint/otlp/v1/logs" || true
curl --max-time 10 --silent --output /dev/null "$endpoint/auth/local/login?code=smoke-login-code&state=smoke-login-state" || true
"${compose[@]}" logs caddy > "$scratch/caddy.log" 2>&1
if ! grep -q '"uri":"/otlp/v1/logs"' "$scratch/caddy.log" || ! grep -q 'code=REDACTED&state=REDACTED' "$scratch/caddy.log"; then
    echo 'FAIL: Caddy did not log the failed requests with redaction' >&2
    exit 1
fi
if grep -q -F -e "$token" -e smoke-login-code -e smoke-login-state "$scratch/caddy.log"; then
    echo 'FAIL: Caddy logged an ingest token or sign-in code' >&2
    exit 1
fi
echo 'PASS: Caddy error logs omit ingest tokens and sign-in codes'
