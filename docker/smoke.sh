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
    "${compose[@]}" --profile postgres --profile redis --profile nginx down -v --remove-orphans >/dev/null 2>&1 || true
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
  nginx:
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
path.write_text(path.read_text().replace("  api_port: 4001", "  base_url: https://smoke.example.test\n  api_port: 443"))
PY
mkdir "$scratch/nginx"
cp docker/nginx/tokenage.conf.example "$scratch/nginx/tokenage.conf"
# Static second address models an independently hosted node. Start sequentially
# below so the two entrypoints cannot race schema migrations.
chmod 644 "$scratch/nginx/tokenage.conf"
"${compose[@]}" --profile postgres --profile redis --profile nginx up -d --wait postgres redis
"${compose[@]}" --profile postgres --profile redis --profile nginx up -d --wait tokenage
"${compose[@]}" --profile postgres --profile redis up -d --wait second
second_ip=$(docker inspect --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$("${compose[@]}" ps -q second)")
python3 - "$scratch/nginx/tokenage.conf" "$second_ip" <<'PYCONFIG'
import sys
from pathlib import Path
path = Path(sys.argv[1])
config = path.read_text()
active = "\n".join(line.split("#", 1)[0] for line in config.splitlines())
assert "proxy_next_upstream error timeout;" in active
assert "non_idempotent" not in active, "Sent POSTs must never be replayed"
path.write_text(config.replace("# server 192.168.x.y:<api_port>", f"server {sys.argv[2]}:4001"))
PYCONFIG
"${compose[@]}" --profile nginx up -d nginx
wait_ready nginx 80
# nginx must not become a tokenless local owner, even with spoofed loopback headers.
status=$(curl --max-time 10 --silent --output /dev/null --write-out '%{http_code}' \
    -H 'Host: localhost' -H 'X-Forwarded-For: 127.0.0.1' "$endpoint/usage")
[ "$status" = 401 ]
ingest_and_assert
echo 'PASS: nginx + Redis + Postgres OTLP ingest and auth isolation'
# Installer origin is operator-configured, never taken from request headers.
curl --max-time 5 --fail --silent -H 'Host: other.example.test' "$endpoint/install.sh" |
    python3 -c "import sys; assert 'TOKENAGE_INSTALL_SERVER=https://smoke.example.test:443\n' in sys.stdin.read()"
"${compose[@]}" exec -T tokenage python - <<'PY'
import os
from pathlib import Path
from src.shared_state import get_shared_state
assert os.getuid() == 10001
assert not Path('/app/client').exists() and not Path('/app/plugins').exists()
assert 'redis' in type(get_shared_state()).__name__.lower()
PY
# Refresh startup-resolved upstream addresses after container replacement.
"${compose[@]}" up -d --force-recreate tokenage
wait_ready tokenage 4001
# Save by rename like an editor: a directory mount must expose the new inode.
python3 - "$scratch/nginx/tokenage.conf" <<'PYCONFIG'
import sys
from pathlib import Path
path = Path(sys.argv[1])
replacement = path.with_suffix(".new")
replacement.write_text(path.read_text().replace("listen 80;", 'listen 80;\n    add_header X-Smoke-Reload "new-inode" always;'))
replacement.replace(path)
PYCONFIG
"${compose[@]}" exec -T nginx nginx -t
"${compose[@]}" exec -T nginx nginx -s reload
wait_ready nginx 80
for _ in $(seq 30); do
    if curl --max-time 5 --fail --silent --dump-header - --output /dev/null "$endpoint/version" | tr -d '\r' | grep -qi '^X-Smoke-Reload: new-inode$'; then break; fi
    sleep 1
done
curl --max-time 5 --fail --silent --dump-header - --output /dev/null "$endpoint/version" | tr -d '\r' | grep -qi '^X-Smoke-Reload: new-inode$'
assert_row
echo 'PASS: configured installer origin, Redis store, non-root image and nginx reload after atomic config save'

# Six sequential functional requests, never a load test. With the primary
# stopped, nginx must retry an unsent POST on the independently addressed node.
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
