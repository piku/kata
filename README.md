# kata

> Kata (型), meaning "form," "model," or "pattern." This aligns with the structured approach of systemd unit files, Dockerfiles/Quadlets for Podman, and Caddyfiles, representing the defined "forms" for deploying and managing applications

# Requirements

`kata` requires a system with `systemd`, `docker`, and `caddy` installed. It is designed to work with the following versions:

- `systemd`: 239 or later
- `docker`: 20.10 or later
- `caddy`: 2.4 or later
- `podman-compose`: 1.0 or later

This means that Debian 13 (Trixie) or later is a requirement.

## Caddy Configuration

Kata uses Caddy's API to configure routing and proxying for your applications. To customize how Caddy handles your application, add a `caddy:` section to your app's `kata-compose.yaml` at the application root directory.

The `caddy:` section supports environment variable substitution. Variables like `$PORT`, `$BIND_ADDRESS`, or any other environment variable defined in your application's ENV file can be used in the configuration.

### How kata applies the `caddy:` section

- Add a top-level `caddy:` key in your `kata-compose.yaml`.
- On deploy, kata:
  - Loads the YAML and substitutes environment variables like `$PORT`, `$APP_ROOT`, etc.
  - Validates the shape (must be a Caddy “server” object, not the full Caddy config)
  - Writes your app’s server config into Caddy at path `/config/apps/http/servers/{app}`
- On destroy, kata removes that server from Caddy while preserving any other servers.

Important:

- Your `caddy.json` must represent a single Caddy HTTP server object (fields like `listen`, `routes`, `tls_connection_policies`, …), not the global Caddy config.
- The server name (ID) is the app name. Each app owns one server: `/apps/http/servers/<app>`.
- Kata does not generate ports/hostnames/SSL; your `caddy.json` should define those (Caddy will handle TLS automatically when configured to do so).

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

When you deploy an app with kata, it's organized as follows:

- **App Code**: `~/.kata/apps/your-app` - Your application's code is deployed here from your git repository
- **Data Directory**: `~/.kata/data/your-app` - Persistent data storage that survives new deployments
- **Environment Settings**: `~/.kata/envs/your-app/ENV` - Environment variables
- **Logs**: `~/.kata/logs/your-app` - Log files for your application
- **Cache**: `~/.kata/cache/your-app` - Cache storage

Your application's Procfile, ENV file, and caddy.json should be placed in the app code directory (they are typically part of your git repository).

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

Serve a web application with static assets and caching:

```yaml
caddy:
  listen:
    - ":$PORT"
  routes:
    - match:
        - host: ["$DOMAIN_NAME"]
      handle:
        - handler: vars
          root: "$APP_ROOT/static"
        - handler: file_server
          match:
            - path: ["/static/*"]
          root: "{http.vars.root}"
          index_names: ["index.html"]
          headers:
            response:
              set:
                Cache-Control: ["public, max-age=3600"]
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
        - handler: basicauth
          hash: { algorithm: bcrypt }
          accounts:
            - username: "$ADMIN_USER"
              password: "$ADMIN_PASS_HASH"
        - handler: reverse_proxy
          upstreams:
            - dial: "$BIND_ADDRESS:$PORT"
```

Note: For `basicauth`, use a pre-hashed password (bcrypt). You can generate one with `caddy hash-password --algorithm bcrypt`.

## Environment Variables

Make sure to set these key environment variables in your application's ENV file:

- `PORT`: Required. The port your application listens on.
- `BIND_ADDRESS`: The address to bind to (defaults to 127.0.0.1).
- `DOMAIN_NAME`: For HTTPS configurations, set this to your domain name.

You can also use these kata-specific variables in your caddy.json:

- `$APP_ROOT`: App directory path (typically ~/.kata/apps/your-app) - use for files in your git repository
- `$DATA_ROOT`: App data directory (typically ~/.kata/data/your-app) - use for persistent data that survives deployments
- `$LOG_ROOT`: Log directory (typically ~/.kata/logs/your-app)
- `$CACHE_ROOT`: Cache directory (typically ~/.kata/cache/your-app)

For more detailed Caddy configurations, refer to the [Caddy JSON documentation](https://caddyserver.com/docs/json/).

### Caddy API Integration

Kata interacts with Caddy through its admin API, which by default runs on `localhost:2019`. When you deploy an app, if a `caddy.json` file is present:

1. The file is loaded and environment variables are expanded
2. The configuration is sent to Caddy's API endpoint at `/config/apps/http/servers/your-app`
3. When an app is destroyed, its configuration is removed via the API

Make sure Caddy is running with its admin API enabled. The default configuration in kata assumes Caddy is started with:

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

Kata can deploy either with Docker Compose (single-host) or Docker Swarm (stack deploy):

- If Swarm is active on the host (`docker swarm init` done), kata uses Swarm by default
- If Swarm is not active, kata uses Compose by default

You can force the mode per app in `kata-compose.yaml`:

```yaml
x-kata-mode: compose  # or 'swarm'
```

Or switch it via CLI:

```bash
# Show current mode
kata mode <app>

# Set to compose (and restart the app)
kata mode <app> compose

# Set to swarm (requires 'docker swarm init' on the host)
kata mode <app> swarm
```

Notes:

- Compose mode uses `docker compose up -d` (falls back to `docker-compose` if needed)
- Swarm mode uses `docker stack deploy ... --prune`
- Keep your `kata-compose.yaml` compatible with your chosen mode (Swarm ignores some Compose-only features and vice versa)

### Secrets

Docker secrets are a Swarm-only feature. The following commands require Swarm:

```bash
kata secrets:set NAME=VALUE      # or NAME=@file, NAME=- (stdin), or just NAME (prompt)
kata secrets:ls
kata secrets:rm NAME
```

If Swarm isn’t active, these commands will error with a helpful message.

