# Kata Manual

A practical guide to using Kata (a tiny micro-PaaS) to deploy and manage your apps with Docker and Caddy.

## What is Kata?

Kata is a single-file deployment tool that lets you:

- Push an app via git (like Piku/Heroku-style) or manage it locally
- Start services with Docker Compose or Docker Swarm
- Configure HTTP/HTTPS routing via Caddy's admin API

It reads a `kata-compose.yaml` in your app repo, prepares a Docker Compose file, optionally updates Caddy, and starts your stack.

## Requirements

- Python 3.12+
- Docker installed (Compose V2 preferred; V1 `docker-compose` supported)
- Optional: Docker Swarm (for stacks and secrets)
- Caddy running on the same host with the Admin API enabled at `localhost:2019`

Notes:

- Kata auto-detects Swarm. If Swarm is active, default mode is `swarm`; otherwise it uses `compose`.
- Kata expects to be run on the same host where Docker and Caddy are running.

## Paths and app layout

Kata manages per-app folders under a configurable root (defaults shown):

- APP_ROOT: `~/app/APP` — your checked-out code
- DATA_ROOT: `~/data/APP` — persistent data
- CONFIG_ROOT: `~/config/APP` — app config (.env, etc.)
- ENV_ROOT: `~/envs/APP` — runtime environment (e.g., Python venv)
- GIT_ROOT: `~/repos/APP` — bare git repo for pushes
- LOG_ROOT: `~/logs/APP` — reserved for logs

These are bound into your containers as:

- /app, /data, /config, /venv

## Install and initial setup

1) Place `kata.py` on your host and make it executable.
2) Run: `kata setup` — creates the root folders listed above.
3) Optional SSH setup for git-push deploys:

   - Add your SSH public key to Kata with: `kata setup:ssh ~/.ssh/id_rsa.pub`
   - This installs a forced-command entry in `~/.ssh/authorized_keys`.

After SSH setup, you can add a git remote like `user@host:myapp` and `git push` to deploy (see Deploy section).

## Your app repository

At minimum, your repo should contain:

- `kata-compose.yaml` — the deployment spec (services and optional `caddy:`)
- Your application code under the repo root (mounted at `/app` in containers)
- Optional: `requirements.txt` (Python), `package.json` (Node), etc.

Kata supports a simple "runtime" shortcut for Python/Node when you don't provide an image; see Compose spec below.

## Compose specification (kata-compose.yaml)

Top-level keys supported by Kata:

- environment: map of defaults applied to all services (merged, without overriding service-defined values)
- services: standard Compose services (images, commands, env, ports, volumes, etc.)
- caddy: a Caddy server object for HTTP routing (applied under Caddy apps.http.servers[app])
- x-kata-mode: optional override of `compose` or `swarm` for this app

Notes:

- Kata expands environment variables in the YAML (e.g., `$APP_ROOT`, `$DATA_ROOT`, `$PORT`).
- Volumes are auto-bound to `/app`, `/config`, `/data`, `/venv` unless you override.
- If you set `runtime: python` or `runtime: nodejs` on a service and omit `image:`, Kata builds a tiny base image and prepares `/venv` or npm install accordingly.

Minimal example (forced compose mode for loopback bind):

```yaml
# kata-compose.yaml
x-kata-mode: compose
environment:
  PORT: 8000
  DOMAIN_NAME: localhost

services:
  web:
    runtime: python
    command: uvicorn main:app --host 0.0.0.0 --port $PORT
    ports:
      - "127.0.0.1:$PORT:$PORT"

caddy:
  listen: [":80", ":443"]
  routes:
    - match: [{host: ["$DOMAIN_NAME"]}]
      handle:
        - handler: reverse_proxy
          upstreams: [{dial: "$BIND_ADDRESS:$PORT"}]
```

For many more Caddy configuration examples, see the Caddy section in `README.md`.

## Caddy integration

- If your `kata-compose.yaml` contains a top-level `caddy:` object, Kata will merge it into Caddy under `apps.http.servers[APP]` using the Admin API (`POST /load`).
- On `kata rm APP`, Kata removes only that server entry and leaves the rest of your Caddy config intact.
- Caddy handles hostnames, HTTP->HTTPS redirects, and TLS according to your `caddy:` config.

Troubleshooting:

- Ensure Caddy is running with the Admin API at `localhost:2019`.
- Check `kata config:caddy APP` to see what was applied.

## Deployment modes: compose vs swarm

- Default: `swarm` if Docker Swarm is active; otherwise `compose`.
- Override per app: add `x-kata-mode: compose|swarm` to `kata-compose.yaml`, or use `kata mode APP [compose|swarm]` (persists in `.kata-mode`).
- Secrets are Swarm-only; attempting to use them without Swarm will show an error.

## Deploying your app

You can deploy either by pushing via git or by preparing a working tree manually.

Option A: Git push

- Ensure SSH is set up with `kata setup:ssh ...`
- In your app repo: add a remote like `user@host:myapp` and push. Kata will clone to `APP_ROOT/APP`, parse `kata-compose.yaml`, apply Caddy (if present), select mode, and start the stack.

Option B: Manual work tree

- Create `APP_ROOT/APP` and put your code and `kata-compose.yaml` there.
- Initialize `GIT_ROOT/APP` and `APP_ROOT/APP` similarly to the automated flow, or trigger an initial deploy by running the internal hook manually: `kata git-hook APP` with a git revision on stdin (advanced).

After deployment, Kata writes a generated Compose file to `APP_ROOT/APP/.docker-compose.yaml`.

Redeploy shortcut (already checked out):

```bash
kata restart APP
```

## Command reference

- ls — list deployed apps (asterisk indicates running)
- config:stack APP — show `kata-compose.yaml`
- config:docker APP — show generated `.docker-compose.yaml`
- config:caddy APP — show live Caddy server config for the app
- restart APP — restart the app
- stop APP — stop the app
- rm [-w|--wipe] APP — remove app (and optionally wipe data/config)
- mode APP [compose|swarm] — get/set app mode and restart to apply
- docker ... — pass-through to `docker` CLI (e.g., logs, ps, exec)
- docker:services STACK — list services in a Swarm stack
- ps SERVICE... — `docker service ps` for Swarm services
- run SERVICE COMMAND... — `docker exec -ti` into a running container
- secrets:set name=value|name=@file|name=-|name — create/replace secret (Swarm only)
- secrets:ls — list Docker secrets (Swarm only)
- secrets:rm NAME — remove a Docker secret (Swarm only)
- setup — create Kata root folders
- setup:ssh PUBLIC_KEY_FILE|- — add SSH key for git deploys
- update — update `kata.py` from the reference URL
- help — show CLI help

Notes:

- The `docker` pass-through and `run` require knowledge of service/container names. When in doubt, check `docker ps` and `docker stack services APP`.

## Logs and troubleshooting

Log access (no built-in aggregation yet):

* Compose mode: `docker compose -f APP_ROOT/APP/.docker-compose.yaml logs -f`
* Swarm: `docker service ps APP_web` then `docker logs <container>`
* Generic: `kata docker logs <container>` (pass-through)

Common issues:
* Caddy config missing: Confirm top-level `caddy:` exists and `kata config:caddy APP` returns JSON
* Port not reachable: Check mode (Swarm may not honor 127.0.0.1 binds); force compose with `kata mode APP compose`
* Secrets error: Initialize Swarm or avoid secrets commands
* Runtime install problems: Ensure `requirements.txt` (Python) or `package.json` (Node) exists; inspect image build output

## Environment variables available to your services

Kata provides these standard variables to each service unless you override them:

- PUID, PGID
- APP_ROOT, DATA_ROOT, CONFIG_ROOT, ENV_ROOT, GIT_ROOT, LOG_ROOT (app-specific paths)

You can reference them in `kata-compose.yaml` or your service commands, e.g., `$APP_ROOT`, `$DATA_ROOT`, `$PORT`.

Merge order (later overrides earlier): base → top-level `environment:` → `ENV` / `.env` → service env.

## Uninstalling an app

- Stop and remove: `kata rm APP`
- Add `--wipe` to also remove `DATA_ROOT/APP` and `CONFIG_ROOT/APP`.

## Safety and notes

- Kata merges only the per-app server into Caddy; it won’t touch other servers you manage in Caddy.
- In `compose` mode, Docker secrets aren’t available; prefer environment variables or config files.
- In `swarm` mode, bind mounts are still used for app/data/config/venv paths by default.

Advanced Caddy example (static + proxy):

```yaml
caddy:
  listen: [":80"]
  routes:
    - match: [{path: ["/static/*"]}]
      handle:
        - handler: file_server
          root: "$APP_ROOT/static"
          headers:
            response:
              set:
                Cache-Control: ["public, max-age=3600"]
    - handle:
        - handler: reverse_proxy
          upstreams: [{dial: "$BIND_ADDRESS:$PORT"}]
```

## Where next

* See `README.md` for extended Caddy examples & advanced scenarios
* Browse `docs/SPEC-revised.md` for current capabilities & roadmap
* Inspect generated Compose: `APP_ROOT/APP/.docker-compose.yaml`
