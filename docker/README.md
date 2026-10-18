# Docker Deployment

Run the server (API, dashboard, and the OTLP collector at `/otlp`) in a single container. Useful for NAS or remote server deployment so you don't need to keep your development machine on.

## Prerequisites

- Docker and Docker Compose
- A `config.yaml` with your remote database URL

## 1. Prepare your config

Copy your existing config or create one with a remote database. Place it at `./config.yaml` in the repo root (or adjust the volume path in `docker-compose.yml`):

```yaml
server:
  # base_url: agents on other machines use this to reach the API/OTLP
  # e.g. http://my-nas or http://my-nas.tailnet.ts.net
  # base_url: http://my-nas
  host: 127.0.0.1  # auto-patched to 0.0.0.0 inside the container
  api_port: 4001

db:
  url: postgresql+psycopg://user:password@db-host:5432/tokenage
```

If no `config.yaml` is found, the entrypoint creates one from `config.example.yaml` (SQLite, not recommended for Docker).

## 2. Build and start

```bash
docker compose up -d
```

Check logs:

```bash
docker logs -f tokenage
```

## 3. Update agent configs (remote deployment)

If the container runs on a different machine (e.g., a NAS over Tailscale), set `server.base_url` in your `config.yaml` to the remote host:

```yaml
server:
  base_url: http://your-nas.tailnet.ts.net
```

Then sign your local machine in to it (`tokenage login --server http://your-nas.tailnet.ts.net:4001`), which points detected agents at its OTLP endpoint, or set them manually:

```bash
# OTLP (for Claude Code, Codex)
export OTEL_EXPORTER_OTLP_LOGS_ENDPOINT=http://your-nas.tailnet.ts.net:4001/otlp/v1/logs
```

Dashboard: open `http://your-nas.tailnet.ts.net:4001` in your browser.

## How it works

- The entrypoint runs schema migrations on startup, then starts the server (API, dashboard, and the OTLP collector at `/otlp`) under supervisord.
- `server.host` is automatically patched from `127.0.0.1` to `0.0.0.0` inside the container for proper Docker networking.
- The container exposes port 4001 (API + dashboard + OTLP at `/otlp`).
- Logs go to stdout/stderr — use `docker logs tokenage` to view them.

## Unraid

Install the Docker Compose Manager plugin from Community Applications, then use `docker compose up -d` via SSH.

## Files

| File | Purpose |
| --- | --- |
| `Dockerfile` | Python 3.13-slim image, installs deps, copies code |
| `docker-compose.yml` | Single service, one port, config volume mount |
| `docker/entrypoint.sh` | Patches host, runs migrations, starts supervisord |
| `docker/supervisord.conf` | Runs the API gunicorn process (OTLP mounted at `/otlp`) |
