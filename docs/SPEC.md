# Kata: Current Implementation Specification

> This document reflects **what `kata.py` implements today**. Earlier design goals (systemd units, Podman quadlets, richer schema) are not yet present; they appear below under “Planned / Roadmap”.

## Overview

Kata is a single‑file micro-PaaS that deploys applications from git pushes (or manual triggers) onto Docker using either **Swarm stacks** or **Compose** and (optionally) configures **Caddy** for HTTP(S) routing via its Admin API. It provides a minimal opinionated layer: parse a `kata-compose.yaml`, generate a `.docker-compose.yaml`, ensure runtime images, deploy, and optionally register a Caddy server block.

Key properties:
* Single Python 3.12+ script (`kata.py`), no external DB
* Uses bind‑mounted host directories instead of named volumes (unless supplied by user)
* Lightweight runtime image bootstrap for Python / NodeJS when `runtime:` is declared per service
* Deterministic environment merging with per‑service overrides
* Optional Caddy server configuration injection (add / remove only your app’s server)
* Git push deployment via internal `git-*` commands and forced authorized_keys commands

Non‑goals (current state): process scaling beyond Docker primitives, declarative build phases, systemd or Podman orchestration, multi-node scheduling beyond Swarm’s native behavior, advanced TLS/cloud provider automation.

## Core Architecture (Implemented)

### Components

1. **Git-based deployment**
   * Bare repo under `$KATA_ROOT/repos/<app>`
   * `post-receive` hook invokes `kata.py git-hook <app>`
   * Hook triggers `do_deploy()` which refreshes working tree and regenerates config
2. **Container orchestration**
   * Docker Swarm (`docker stack deploy`) if Swarm active, else Docker Compose (`docker compose up -d`)
   * Per‑app mode selectable (`kata mode <app> [compose|swarm]` or `x-kata-mode:` key)
3. **Runtime images**
   * On-demand build of `kata/python` or `kata/nodejs` when a service defines `runtime: python|nodejs`
   * Injects volumes: app, config, data, venv
   * Python runtime creates venv and installs `requirements.txt` if present
4. **Environment merging**
   * Base (paths + PUID/PGID) → top-level `environment:` → config file `ENV` / `.env` → service environment (list or mapping) with service keys winning
5. **Caddy integration**
   * Optional top-level `caddy:` server object injected into existing Caddy configuration at `/apps/http/servers/<app>` via Admin API
   * Removal cleans only that server entry
6. **Secrets (Swarm only)**
   * Simple passthrough to `docker secret` commands (`secrets:set|ls|rm`) gated by Swarm detection

## Configuration Format (kata-compose.yaml)

`kata-compose.yaml` is a **Compose-like** YAML. Kata reads a subset sufficient to:
* Determine services and their runtime/image/command/ports/volumes/environment
* Optionally capture a `caddy:` server object (NOT full Caddy root config)
* Optionally set deployment mode via `x-kata-mode: compose|swarm`

Unsupported (currently ignored) top-level keys from earlier design: `version`, `build`, `app`, `networks`, `volumes` (unless user supplies explicit volumes mapping which is passed through), custom scaling fields (`instances`), scheduled tasks, CloudFlare settings.

### Minimal Structure Example

```yaml
environment:
  PORT: "8000"
  DOMAIN_NAME: "example.test"

services:
  web:
    runtime: python
    command: uvicorn app:app --host 0.0.0.0 --port $PORT
    ports:
      - "127.0.0.1:$PORT:$PORT"  # Compose mode or single-host use

caddy:
  listen: [":80", ":443"]
  routes:
    - match: [{ host: ["$DOMAIN_NAME"] }]
      handle:
        - handler: reverse_proxy
          upstreams: [{ dial: "127.0.0.1:$PORT" }]

x-kata-mode: compose  # optional override
```

### Service Runtime Selection
Provide either:
* `runtime: python|nodejs` (Kata supplies `image:` and mounts) OR
* Explicit `image: repo/name:tag` (no runtime bootstrap performed)

### Environment Forms
Service `environment:` may be:
* Mapping (`KEY: value`)
* List (`["KEY=VALUE", "BARE_KEY"]`) — normalized; bare keys default to empty string

## Directory Structure (Current)

Kata organizes files under `$KATA_ROOT` (default `$HOME`):

| Path | Purpose |
|------|---------|
| `app/<app>` | Working tree (checked out code) |
| `data/<app>` | Persistent data (bind mounted as `data`) |
| `config/<app>` | Config overrides (`ENV` / `.env`) |
| `envs/<app>` | Virtual env / runtime state (`/venv` mount) |
| `logs/<app>` | (Reserved for future log handling; not actively written by kata.py) |
| `repos/<app>` | Bare git repo (push target) |

Generated per deployment: `app/<app>/.docker-compose.yaml`

## Supported Runtimes (Implemented)

Currently implemented:

1. **Python**
   * Debian slim base, installs python3 + venv + pip
   * Creates `/venv`, installs `requirements.txt` if present
2. **NodeJS**
   * Debian slim base, installs node+npm+yarn, runs `npm install`

Planned (not yet): Go, Rust, generic container build orchestration, static site shortcuts.

## `kata-compose.yaml` Reference (Subset)

### Top-level `environment:`

```yaml
environment:
  KEY: value
  PORT: 8000
```

### Services Section

Compose-like structure. Kata uses only a subset:

```yaml
services:
  service_name:
    runtime: python            # OR image: repo/name:tag
    command: your start cmd
    ports:
      - "127.0.0.1:8000:8000"
    volumes:                   # optional custom mapping
      - app:/app               # if omitted Kata injects default named bind volumes
    environment:               # optional, list or mapping
      KEY: value
```

### Caddy Section (Server Object Only)

Provide a single **server object** (NOT full root config):

```yaml
caddy:
  listen: [":80", ":443"]
  routes:
    - match:
        - host: ["example.com"]
      handle:
        - handler: reverse_proxy
          upstreams:
            - dial: "service_name:8000"
  automatic_https:
    disable: false
```

### Automatic Volume Binding
If the top-level YAML lacks a `volumes:` mapping, Kata creates one with four bind mounts (`app`, `config`, `data`, `venv`) pointing to host paths. A service without explicit `volumes:` gets default shorthand mounts (`app:/app`, etc.).

## Caddy Integration (Implemented Path)

1. Load YAML, expand env vars
2. Extract `caddy:` server object (validate minimal structure: list types etc.)
3. GET current full Caddy config (`/config/`)
4. Insert/replace `apps.http.servers[app]` with provided object; POST to `/load`
5. On removal, delete that key and POST updated config

No automatic derivation of routes, TLS policies, or redirects is performed; you supply them.

## Environment Merging Rules

Order of precedence (last wins):
1. Base variables (PUID, PGID, path constants per app)
2. Top-level `environment:` in `kata-compose.yaml`
3. `ENV` or `.env` file inside `config/<app>`
4. Service-level environment entries

During service normalization:
* List form is converted to a mapping
* Base variables are added only if not already defined by the service

## Git / SSH Flow

* Authorized public keys appended with forced command referencing `kata.py`
* `git-receive-pack` / `git-upload-pack` are passthrough commands used internally
* After push, `git-hook` receives refs and triggers `do_deploy`

## CLI (Implemented Commands)

| Command | Summary |
|---------|---------|
| setup | Create root directory skeleton |
| ls | List apps (mark running) |
| config:stack <app> | Show original `kata-compose.yaml` |
| config:docker <app> | Show generated `.docker-compose.yaml` |
| config:caddy <app> | Show Caddy server JSON for app |
| restart / stop / rm <app> | Lifecycle operations |
| mode <app> [mode] | Get/set deployment mode |
| secrets:set/ls/rm | Manage Swarm secrets (Swarm only) |
| docker ... | Passthrough to `docker` |
| docker:services <stack> | List services in a stack |
| ps <service...> | Show tasks for a service (Swarm) |
| run <service> <cmd...> | Exec inside a container |
| setup:ssh <pubkey> | Register SSH key (forced command) |
| update | Attempt self-update from upstream source |

Not implemented (earlier spec): deploy, logs, config:set/unset, validate, migrate, scaling flags, schedule.

## Deployment Sequence (Actual)

1. Git push triggers `git-hook`
2. Update working tree (`git fetch/reset/submodule update`)
3. Parse `kata-compose.yaml` → merge env → build runtime image if needed → write `.docker-compose.yaml`
4. Inject Caddy server (if provided)
5. Deploy via Swarm or Compose (mode logic)

## Security (Current)

* SSH key forced-command restrictions
* Docker isolation only (no systemd sandboxing / Podman yet)
* Caddy TLS automation only if your server object config triggers it (standard Caddy behavior)

## Logging (Current)

* Kata itself prints to stdout / stderr
* Container logs accessible via `docker` / `kata docker ...` commands (no integrated log aggregation)

## Examples

### Simple Python Web Application Without Caddy
```yaml
version: "1.0"

app:
  name: flask-app
  runtime: python

environment:
  FLASK_ENV: production
  PORT: 8000

build:
  commands:
    - pip install -r requirements.txt

services:
  web:
    command: gunicorn app:app --bind 0.0.0.0:$PORT
    restart: always
```

### Simple Python Web Application with Caddy
```yaml
version: "1.0"

app:
  name: flask-app
  runtime: python

environment:
  FLASK_ENV: production
  PORT: 8000

build:
  commands:
    - pip install -r requirements.txt

services:
  web:
    command: gunicorn app:app --bind 127.0.0.1:$PORT
    restart: always

caddy:
  routes:
    - match:
        - host: ["flask.example.com"]
      handle:
        - handler: reverse_proxy
          upstreams:
            - dial: ":$PORT"
  https:
    enabled: true
    redirect: true
    domains: ["flask.example.com"]
```

### Multi-Service Microservices Application
```yaml
version: "1.0"

app:
  name: microservices-app

environment:
  DATABASE_URL: postgresql://user:pass@db:5432/app
  REDIS_URL: redis://redis:6379

services:
  web:
    image: nginx:alpine
    ports:
      - "8080:80"
    volumes:
      - "./nginx.conf:/etc/nginx/nginx.conf"
    depends_on:
      - api

  api:
    command: node server.js
    instances: 3
    environment:
      NODE_ENV: production
    healthcheck:
      path: /api/health
    depends_on:
      - db
      - redis

  db:
    image: postgres:15
    volumes:
      - "db_data:/var/lib/postgresql/data"
    environment:
      POSTGRES_DB: app
      POSTGRES_PASSWORD: ${DB_PASSWORD}

  redis:
    image: redis:7-alpine

  worker:
    command: node worker.js
    instances: 2
    depends_on:
      - redis
      - db

volumes:
  db_data:

caddy:
  routes:
    - match:
        - host: ["api.example.com"]
      handle:
        - handler: reverse_proxy
          upstreams:
            - dial: "api:3000"
    - match:
        - host: ["example.com"]
      handle:
        - handler: reverse_proxy
          upstreams:
            - dial: "web:80"
  https:
    enabled: true
    domains: ["example.com", "api.example.com"]
```

### Static Site with Build Process
```yaml
version: "1.0"

app:
  name: static-site
  runtime: static

build:
  commands:
    - npm install
    - npm run build

services:
  static:
    type: static
    root: ./dist

caddy:
  routes:
    - match:
        - host: ["example.com"]
      handle:
        - handler: vars
          root: "$HOST_APP_ROOT/dist"
        - handler: file_server
          match:
            - path: ["/assets/*"]
          root: "{http.vars.root}"
          headers:
            response:
              set:
                Cache-Control: ["public, max-age=31536000"]
        - handler: file_server
          root: "{http.vars.root}"
          index_names: ["index.html"]
          try_files: ["{path}", "/index.html"]
  https:
    enabled: true
    redirect: true
```

### Container-Based Application with Custom Networks
```yaml
version: "1.0"

app:
  name: container-app

services:
  app:
    image: myapp:latest
    instances: 2
    ports:
      - "8000:8000"
    networks:
      - app_network
    environment:
      DATABASE_URL: postgresql://db:5432/app
    depends_on:
      - db

  db:
    image: postgres:15
    networks:
      - app_network
    volumes:
      - "postgres_data:/var/lib/postgresql/data"
    environment:
      POSTGRES_DB: app

volumes:
  postgres_data:

networks:
  app_network:
    driver: bridge

caddy:
  routes:
    - handle:
        - handler: reverse_proxy
          upstreams:
            - dial: "app:8000"
  https:
    enabled: true
```

## Limitations (Current Implementation)

* Single-host focus (Swarm optional but used minimally)
* No horizontal scaling flags (`instances`) or healthcheck synthesis
* No build pipeline abstraction; you provide ready-to-run code / image
* No systemd / Podman integration (Docker only)
* No scheduled jobs / timers
* No integrated log rotation or log viewing commands
* Caddy integration limited to injecting supplied server object (no templating / multi-env layering)

## Planned / Roadmap (Focused)

Near‑term priorities only (longer speculative list removed):
1. Additional runtimes (Go, Rust, static) via `runtime:` images
2. Basic scaling & healthcheck fields mapped to Swarm / Compose
3. Log tail / follow helper (`kata logs <app> [service]`)
4. Config mutation commands (`config:set|unset`) with persistence
5. Optional HTTP→HTTPS redirect helper in Caddy injection

---

This document should be updated alongside code changes; discrepancies mean the code is authoritative.

