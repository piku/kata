# kata

> Kata (型) means *form / model / pattern*. This project provides a tiny “micro-PaaS” wrapper around Docker (Compose or Swarm) with optional HTTP routing via Caddy.

## What it does (current implementation)

* Parses an application `kata-compose.yaml` and generates a `.docker-compose.yaml` used for deployment
* Supports either Docker Swarm (`docker stack deploy`) or Docker Compose (`docker compose up -d`) per app (auto‑selects, overridable)
* Optionally configures Caddy via its Admin API using a top‑level `caddy:` section inside `kata-compose.yaml`
* Builds lightweight runtime images on‑demand for `runtime: python` or `runtime: nodejs` services (images: `kata/python`, `kata/nodejs`)
* Manages per‑app bind‑mounted directories (code, data, config, venv, logs, repos)
* Merges environment variables from multiple sources and injects them into each service
* Provides simple git push deployment hooks (`git-receive-pack` / `git-hook`)
* Offers helper commands for secrets (Swarm only), mode switching, and Caddy inspection

## Requirements

Mandatory:

* **Docker** 20.10+ (Swarm optional; if inactive, Compose mode is used)
* **Python** 3.12+ (to run `kata.py`)

Optional (only if you want HTTP routing):

* **Caddy** 2.4+ with Admin API enabled (`admin localhost:2019`)

> systemd / Podman are **not** required by the current code path (earlier design notes referenced them).

Tested on Debian 12/13 and recent Ubuntu; any Linux with Docker + Caddy should work.

## Caddy Configuration

Add a **top-level `caddy:` key** inside `kata-compose.yaml`. Do **not** create a separate `caddy.json` file; the script extracts the object and injects it into the live Caddy config under `/apps/http/servers/<app>`.

Environment variables inside that section are expanded (shell style), using the merged per‑app environment.

### How it’s applied

On deploy:
1. `kata-compose.yaml` is loaded and variable substitution runs.
2. The `caddy:` object (must represent a single **server** block) is merged into the existing Caddy config only at your app server key.
3. Other servers remain untouched.

On destroy: only your app’s server entry is removed.

Important:

* Supply a **server object** (fields like `listen`, `routes`, `tls_connection_policies`, etc.). Not a full Caddy root config.
* Server name == app name.
* Kata does not invent hostnames / TLS settings—configure them yourself; Caddy auto‑enables HTTPS when host matchers use real domains.

### Environment variables available in the `caddy:` section

Common variables you can reference as `$VARNAME`:

- `$PORT` (required): Upstream app port (your service must listen on this)
- `$BIND_ADDRESS`: Upstream bind address (default 127.0.0.1)
- `$DOMAIN_NAME`: Hostname for HTTPS/virtual hosts
- `$APP_ROOT`: App code directory (e.g., `~/.kata/apps/<app>`) — use when static assets live in the repo
- `$DATA_ROOT`: App data directory (e.g., `~/.kata/data/<app>`) — use for uploaded/generated files
- `$LOG_ROOT`, `$CACHE_ROOT`: Log and cache directories

You can also reference any keys defined in your app’s ENV file.

### Useful Caddy building blocks

- Handlers: `reverse_proxy`, `file_server`, `encode`, `headers`, `static_response`, `respond`, `rewrite`, `route`
- Matchers: `host`, `path`, `path_regexp`
- TLS: automatic HTTPS is enabled by Caddy when you specify hostnames and do not disable it; you can also add `tls_connection_policies`.

Below are ready-to-use examples you can adapt.

### Application Structure

Paths are rooted at `KATA_ROOT` (default: `$HOME`). The current directory names (note: singular `app/`) are:

* Code: `$KATA_ROOT/app/<app>`
* Data: `$KATA_ROOT/data/<app>`
* Config: `$KATA_ROOT/config/<app>` (place `ENV` or `.env` here to override variables)
* Virtual env / runtime state: `$KATA_ROOT/envs/<app>`
* Logs: `$KATA_ROOT/logs/<app>`
* Git bare repos: `$KATA_ROOT/repos/<app>`

There is presently **no** dedicated cache directory constant (earlier docs mentioned one).

Generated file: `.docker-compose.yaml` inside the app code directory (regenerated each deploy).

## Sample `caddy:` sections for `kata-compose.yaml`

### 1. Static File Serving

Serve static files from a directory:

```yaml
caddy:
  listen:
    - ":$PORT"
  routes:
    - handle:
        - handler: file_server
          root: "$APP_ROOT"
          index_names:
            - index.html
            - index.htm
```

> **Note:** Use `$APP_ROOT` when your static files are part of your git repository. Use `$DATA_ROOT` when files are generated or uploaded separately from deployments.

### 2. Basic HTTP Proxy

Proxy requests to your application:

```yaml
caddy:
  listen:
    - ":$PORT"
  routes:
    - handle:
        - handler: reverse_proxy
          upstreams:
            - dial: "$BIND_ADDRESS:$PORT"
          headers:
            request:
              add:
                X-Forwarded-Proto: ["http"]
                X-Real-IP: ["{remote}"]
                X-Forwarded-For: ["{remote}"]
```

### 2b. Reverse proxy with gzip/brotli and basic security headers

```yaml
caddy:
  listen:
    - ":$PORT"
  routes:
    - handle:
        - handler: encode
          encodings:
            gzip: {}
            zstd: {}
        - handler: headers
          response:
            set:
              Strict-Transport-Security: ["max-age=31536000; includeSubDomains"]
              Referrer-Policy: ["no-referrer"]
              X-Content-Type-Options: ["nosniff"]
              X-Frame-Options: ["DENY"]
              X-XSS-Protection: ["0"]
        - handler: reverse_proxy
          upstreams:
            - dial: "$BIND_ADDRESS:$PORT"
```

### 3. HTTPS Configuration

Host your application with automatic HTTPS (requires a public domain and port 443 access):

```yaml
caddy:
  listen:
    - ":443"
  routes:
    - match:
        - host: ["$DOMAIN_NAME"]
      handle:
        - handler: reverse_proxy
          upstreams:
            - dial: "$BIND_ADDRESS:$PORT"
          headers:
            request:
              add:
                X-Forwarded-Proto: ["{http.request.scheme}"]
                X-Real-IP: ["{remote}"]
                X-Forwarded-For: ["{remote}"]
  tls_connection_policies:
    - match:
        sni: ["$DOMAIN_NAME"]
  automatic_https:
    disable: false
```

### 3b. Multiple hostnames (with redirect from www to apex)

```yaml
caddy:
  listen:
    - ":443"
  routes:
    - match:
        - host: ["www.$DOMAIN_NAME"]
      handle:
        - handler: static_response
          headers:
            Location: ["https://$DOMAIN_NAME{http.request.uri}"]
          status_code: 301
    - match:
        - host: ["$DOMAIN_NAME"]
      handle:
        - handler: reverse_proxy
          upstreams:
            - dial: "$BIND_ADDRESS:$PORT"
  automatic_https:
    disable: false
```

### 4. Advanced Configuration (Static Assets with Cache)

Serve versioned/static assets under `/static/*` with caching + proxy everything else:

```yaml
caddy:
  listen:
    - ":$PORT"
  routes:
    # Static assets (cacheable)
    - match:
        - path: ["/static/*"]
      handle:
        - handler: file_server
          root: "$APP_ROOT/static"
          index_names: ["index.html"]
          headers:
            response:
              set:
                Cache-Control: ["public, max-age=3600"]
    # Everything else -> upstream app
    - handle:
        - handler: reverse_proxy
          upstreams:
            - dial: "$BIND_ADDRESS:$PORT"
```

### 5. Split routing: static at /, API under /api -> upstream

```yaml
caddy:
  listen:
    - ":$PORT"
  routes:
    - match:
        - path: ["/api/*"]
      handle:
        - handler: reverse_proxy
          upstreams:
            - dial: "$BIND_ADDRESS:$PORT"
    - handle:
        - handler: file_server
          root: "$APP_ROOT/public"
          index_names: ["index.html"]
```

### 6. Basic auth for an admin path

```yaml
caddy:
  listen:
    - ":$PORT"
  routes:
    - match:
        - path: ["/admin/*"]
      handle:
        # Basic HTTP auth (bcrypt-hashed password)
        - handler: authentication
          providers:
            http_basic:
              accounts:
                - username: "$ADMIN_USER"
                  password: "$ADMIN_PASS_HASH"  # bcrypt hash
        - handler: reverse_proxy
          upstreams:
            - dial: "$BIND_ADDRESS:$PORT"
```

Generate a bcrypt hash with: `caddy hash-password --algorithm bcrypt --plaintext 'secret'`.

## Environment Variables

Merged from (later sources override earlier):
1. Base: `PUID`, `PGID`, and per‑app root paths (`APP_ROOT`, `DATA_ROOT`, `ENV_ROOT`, `CONFIG_ROOT`, `GIT_ROOT`, `LOG_ROOT`)
2. Top‑level `environment:` mapping in `kata-compose.yaml` (optional)
3. `ENV` or `.env` file in the app’s config directory
4. Service‑level `environment` entries

Compose list form (`["KEY=VALUE", "BARE_KEY"]`) is normalized; bare keys default to empty string.

Recommended to set:
* `PORT` (service listen port, especially for reverse proxying)
* `BIND_ADDRESS` (default `127.0.0.1` if omitted in your own config logic)
* `DOMAIN_NAME` (for host matchers / TLS)

Automatically injected into each service unless already set: the base variables above.

For more details on Caddy JSON, see the [Caddy docs](https://caddyserver.com/docs/json/).

### Caddy API Integration

Kata talks to the Caddy Admin API at `localhost:2019`:

1. Extracts the `caddy:` server object from `kata-compose.yaml`
2. Expands variables
3. Reads current full config, updates only `/apps/http/servers/<app>`
4. POSTs the updated full config to `/load`
5. On removal, deletes that server entry and reposts

Ensure Caddy runs with:

```caddyfile
{
  debug
  admin localhost:2019
}
```

### Troubleshooting Caddy

- Inspect your app’s live Caddy server JSON:

```bash
kata config:caddy <app>
```

- Check that the admin API is reachable (on the host):

```bash
curl -s http://localhost:2019/config | jq '.'
```

- Typical errors:
  - “PORT not set”: ensure `PORT` is defined in your ENV and that your service actually listens on it.
  - “Invalid caddy.json shape”: provide a server object (with `listen`, `routes`, …), not the full global Caddy config.
  - TLS not provisioning: verify DNS, public IP reachability on 80/443, and that `host` matchers use your real domain.

## Compose vs Swarm Modes

Default per host state:
* Swarm active → deploy via `docker stack deploy`
* Swarm inactive → deploy via `docker compose up -d`

Override per app:

```yaml
x-kata-mode: compose   # or swarm
```

Or with CLI:

```bash
kata mode <app>          # show
kata mode <app> compose  # set & restart
kata mode <app> swarm
```

Helper file `.kata-mode` in the app root persists the selection.

### Runtime Images

If a service defines:

```yaml
services:
  web:
    runtime: python  # or nodejs
    command: ["python", "-m", "app"]
```

Kata will build (once) or reuse a `kata/<runtime>` image from an internal Dockerfile, bind‑mount app/config/data/venv, and (for Python) create a venv + install `requirements.txt`.

If you supply `image:` yourself, no runtime automation runs.

### Secrets (Swarm only)

Commands:

```bash
kata secrets:set NAME=VALUE   # NAME=@file, NAME=- (stdin), or just NAME (prompt)
kata secrets:ls
kata secrets:rm NAME
```

They are disabled (with a warning) when Swarm is inactive.

### Git Deployment

Two internal commands (`git-receive-pack` / `git-upload-pack`) plus the `git-hook` are used when you push to a bare repo under `$KATA_ROOT/repos/<app>`. The post‑receive hook triggers `git-hook` which runs `do_deploy`.

You can also manually trigger deployment by piping a synthetic ref update:

```bash
echo "0000000000000000000000000000000000000000 $(git rev-parse HEAD) refs/heads/main" | kata git-hook <app>
```

### CLI Overview

Selected commands (run `kata help` for full output):

| Command | Purpose |
|---------|---------|
| setup | Create root directories |
| ls | List apps & running state |
| restart / stop / rm | Lifecycle management |
| mode | Get/set deploy mode |
| config:stack | Show original `kata-compose.yaml` |
| config:docker | Show generated `.docker-compose.yaml` |
| config:caddy | Show live Caddy server JSON |
| secrets:* | Manage Swarm secrets |
| docker ... | Passthrough to `docker` |
| docker:services / ps | Inspect Swarm/Compose processes |
| run <service> <cmd...> | Exec into a running container |
| update | (WIP) self‑update script |

> `update` currently attempts a raw download; harden before production use.

## Examples

See `docs/examples/minimal-python/` for a small FastAPI app using the Python runtime.

---

Feedback / issues welcome. This README tracks the **current code** in `kata.py`; if something here is missing in code, file a bug.

