# Docker server

From the repository root, run `cd docker`, copy `../config.example.yaml` to
`config.yaml`, then choose a setup below. The image
contains only the server and dashboard; clients install through `install.sh`.
State persists in the `tokenage-data` volume at `/data`. The image runs as UID
10001: mounted config files must be readable by that user (for example, grant
that UID an ACL with `setfacl -m u:10001:r config.yaml`, or use group ownership
and mode 640). Config is never rewritten. Compose 2.24.4 or newer is required.

| Setup | Command | Config |
|---|---|---|
| SQLite | `docker compose up -d` | defaults |
| Existing Postgres | `docker compose up -d` | uncomment host Postgres `db.url` |
| Bundled Postgres | `docker compose --profile postgres up -d` | uncomment bundled `db.url` |
| Bundled Redis | add `--profile redis` | uncomment `queue.url` and `queue.prefix` |
| Existing Redis | no extra profile | set `queue.url` to its address |
| nginx | add `--profile nginx` | copy `nginx/tokenage.conf.example` to `nginx/tokenage.conf` |

Set these options in `config.yaml` for the selected services:

```yaml
# Bundled Postgres; match POSTGRES_PASSWORD in .env:
# db: {url: "postgresql+psycopg://tokenage:CHANGE_ME@postgres:5432/tokenage"}
# Existing Postgres on the Docker host:
# db: {url: "postgresql+psycopg://tokenage:CHANGE_ME@host.docker.internal:5432/tokenage"}
# Bundled Redis:
# queue: {url: "redis://redis:6379/0", prefix: "tokenage:"}
# Public origin behind nginx/TLS:
# server: {base_url: "https://tokenage.example"}
```

Replace the existing `db: {}` and `server` entries instead of adding duplicate
keys. When no database path is configured, SQLite uses `/data/usage.db`.

For bundled Postgres, set `POSTGRES_PASSWORD` in `.env` and put the same password
(percent-encoded) in `db.url`; the development default is `tokenage`. Compose
waits for bundled Postgres and Redis readiness before starting tokenage. Use
`pg_dump` for backups.

Several servers behind nginx must use the same Postgres (`db.url`), Redis
(`queue.url` and `queue.prefix`), and `server.base_url`. Delivery is at-least-once.
SQLite and its default queue are local. Upgrade one server at a time: migrations
do not lock across servers.
Drain the queue before switching stores. Redis is private to the compose network;
provide a protected endpoint separately if other machines need it. For example,
use `compose.override.yml` to bind only a LAN/Tailscale IP owned by this host:

```yaml
services:
  redis:
    ports:
      - "100.x.y.z:6379:6379"
    command: [redis-server, --appendonly, "yes", --appendfsync, everysec,
              --maxmemory-policy, noeviction, --requirepass, "CHANGE_ME"]
    healthcheck:
      test: [CMD-SHELL, 'REDISCLI_AUTH="CHANGE_ME" redis-cli ping']
```

Replace the IP and password, protect the override's permissions, and restrict
network access to the participating servers. Update **every** node's `queue.url`:
`redis://:password@redis:6379/0` inside Compose and
`redis://:password@100.x.y.z:6379/0` on other machines. Percent-encode special
characters in the password when putting it in the URL. Authentication does not
encrypt Redis traffic; use a trusted private network or encrypted tunnel.

First sign-in: `docker compose exec tokenage python -m src.cli login-link`
(the container equivalent of `tokenage server login-link`). Open the printed link,
replacing its origin with your dashboard origin if needed. Set `server.base_url`
to the complete public origin, including a port only if it is non-default, for
URLs handed to clients and `/install.sh`. `server.api_port` is the listen port
for native installs. The installer's origin comes from config, so forwarded
headers do not replace it. Container gunicorn always binds `0.0.0.0:4001`,
regardless of `server.api_port`. Client installation through
`/install.sh` requires HTTPS or localhost; put a TLS proxy or tunnel in front.

Create `nginx/tokenage.conf` from the shipped example before starting the nginx
profile. Compose mounts the entire `nginx/` directory at `/etc/nginx/conf.d`,
so editors that save by rename are picked up on reload. Only active `.conf`
files belong there; keep saved examples with a different suffix. If migrating
from the old single-file mount, move your upstream and server blocks from
`nginx.conf` into `nginx/tokenage.conf`, removing the outer `events {}` and
`http {}` blocks, then recreate nginx to switch its mount.

nginx uses `least_conn`: `server tokenage:4001` reaches the compose container;
add other machines by LAN address (`server 192.168.x.y:<api_port>`) or Tailscale
(`server <tailscale-host>:<api_port> backup`). Edit `nginx/tokenage.conf`, then validate and reload:

```sh
docker compose exec nginx nginx -t
docker compose exec nginx nginx -s reload
```

Old workers finish existing requests. Addresses resolve at startup/reload;
**reload after replacing an upstream container** to pick up its new IP. Keep the
node out of rotation until this reload; its old IP may be reused by Docker. Runtime
`resolve` is deliberately avoided: nginx 1.28 can remove a Docker DNS peer during
a connect timeout and cancel failover. A connect failure can retry another node
before a POST is sent; a POST already sent is never automatically replayed.
The example marks a peer unavailable for 10 seconds after one failure and uses a
1-second connect timeout. Tune `max_fails`, `fail_timeout` and
`proxy_connect_timeout` for your network if a healthy node needs longer to connect.
An interrupted in-flight request can still fail, so clients must handle ambiguous
failures according to the at-least-once delivery contract.

For planned maintenance, first mark that server `backup` (when another primary
is healthy) or remove it from the upstream and reload. Wait for existing requests
to finish before stopping it. Restore the server and reload when healthy.
Requests through nginx do not receive local-owner access: the API sees nginx’s
container address, not loopback.

For an existing Postgres:

- `localhost` means this container. Use `host.docker.internal` for the host
  (mapped on Linux), a service name on the same network, or a reachable hostname.
- Host Postgres needs `listen_addresses` covering the Docker bridge and a
  `pg_hba.conf` rule for the actual Docker subnet.
- Create the database and role first; migrations create tables only:
  `CREATE USER tokenage WITH PASSWORD '...';`
  `CREATE DATABASE tokenage OWNER tokenage;`

Create an ingest token (capture the printed secret privately):

```sh
docker compose exec tokenage python -m src.cli token create --email user@example.com --kind ingest
```

Check containers/logs with `docker compose ps` and `docker compose logs tokenage`.
To inspect queue depth and shared-state status inside the image:

```sh
docker compose exec tokenage python -c 'from src.shared_state import get_shared_state; print(get_shared_state().status())'
```

Pin `TOKENAGE_VERSION` in `.env`. Update one server at a time, using the maintenance
procedure above when several servers share a database/queue:

```sh
docker compose pull tokenage && docker compose up -d tokenage
# Wait for tokenage to become healthy (docker compose ps), then, if using nginx:
docker compose exec nginx nginx -t && docker compose exec nginx nginx -s reload
curl --fail http://localhost:<public-port>/version
```

Verify the expected version and commit, and restore upstream membership before
updating the next server. To roll back, revert the tag in `.env` and repeat these
commands, preserving the volumes. Check schema compatibility before rolling back
across a release that changes the database schema.

Set `TOKENAGE_VERSION` to pin an image; tags include the server version,
`MAJOR.MINOR`, and `latest`. GHCR keeps the newest 20 tagged image versions;
older version pins eventually expire. Set `TOKENAGE_PORT` / `NGINX_PORT` for host ports.
From the repository root, build locally: `docker build --build-arg GIT_COMMIT="$(git rev-parse HEAD)" -t tokenage:local .`.
Run `TOKENAGE_SMOKE_IMAGE=tokenage:local ./docker/smoke.sh` for isolated functional
checks (Postgres ingest, SQLite persistence, nginx + Redis ingest, atomic config saves/reloads, and abrupt-stop POST failover).

### Prometheus metrics

Metrics are off by default. To enable a separate, unauthenticated listener, add
this to the mounted server config (choose a port different from the API):

```yaml
metrics:
  enabled: true
  host: 0.0.0.0
  port: 9108
  allowed_hosts: [tokenage]  # Docker service name used by the scraper
```

Scrape `http://tokenage:9108/metrics` from Grafana Alloy or Prometheus on the same
Docker network. **Do not add this port to compose `ports`, publish it, or proxy it
through the public ingress.** On a VM use `127.0.0.1` for a same-host scraper, or
bind a private/tailnet interface. Let the scraper add `instance` and `provider`
labels; the app does not identify the node. `/metrics` returns 404 on the API
port, and every other path returns 404 on the metrics port. Loopback binds accept
only loopback Host names. For private binds, Host must match `metrics.host` or
an entry in `metrics.allowed_hosts`; add the node IP or MagicDNS name for a
central tailnet scraper. Host ports are ignored.

The listener uses the same Gunicorn workers as the API, with per-run multiprocess
counters under `TOKENAGE_HOME`. Keep Gunicorn's default `preload_app=false`.
After enabling, disabling, or changing the metrics bind address/port, fully restart
the container (with a brief outage); on a VM run `tokenage server stop tokenage-api`
then `tokenage server start`.
An ordinary `tokenage server restart` sends SIGHUP and preserves counters when
the binds are unchanged. It refuses when the running bind differs or is unknown,
because changing listeners via SIGHUP can terminate the Gunicorn master.
Counter and histogram files accumulate by up to two files per retired worker
(at most `2 * server.workers` per reload) until a full master restart;
periodically stop/start long-lived masters. Stale
per-master directories are cleaned at master start.
Scrapes expose build version/commit, ingest request counts, acceptance latency and
errors, committed usage rows by a bounded client source, consumer progress/batch
time, and shared-store queue depth. Queue depth is omitted if its read fails or
takes more than 200 ms; no usage-database query runs during a scrape.
