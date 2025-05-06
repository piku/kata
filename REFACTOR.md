# Piku Refactor - Kata


## Goals

* Switch from `nginx` to `caddy`
* Switch from `uWSGI` to `systemd` and `podman` to run apps
* Support only a subset of the runtimes (Python to begin with)
  * use `systemd --user` and `podman` quadlets to support other runtimes using pre-defined base images
* Build and launch generic containers if a Dockerfile or `docker-compose.yaml`  is present
* Remove the dependency on `click` by replacing it with a similar decorator-based approach based on internal decorators written as functions

## Things that should be retained

* The same coding style and structure
* The SSH deployment and monitoring mechanism
* The ability to serve static files
* The ability to set up HTTPS automatically
* The ability to enable Cloudflare-only inbound IP filtering

## Implemented Features Checklist

### Core Technology Switches:
- [x] Switch from `nginx` to `caddy`
- [x] Switch from `uWSGI` to `systemd` to run apps
- [x] Switch from `uWSGI` to `podman` to run containerized apps

### Runtime Support:
- [x] Support Python runtimes (standard `requirements.txt`, `poetry`, `uv`)
- [x] Support generic containers via `Dockerfile`
- [x] Support `docker-compose.yaml` via `podman-compose` (initial support)
- [ ] Use `systemd --user` and `podman` quadlets for other runtimes (Podman support exists, direct quadlet generation TBD)

### CLI:
- [ ] Remove the dependency on `click` by replacing it with a similar decorator-based approach. (Currently still uses `click`)

### Retained Features:
- [x] The same coding style and structure
- [x] The SSH deployment and monitoring mechanism
- [x] The ability to serve static files (via Caddy)
- [x] The ability to set up HTTPS automatically (via Caddy)
- [x] The ability to enable Cloudflare-only inbound IP filtering (via Caddy)
