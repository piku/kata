# Piku Refactor

> Kata (型), meaning "form," "model," or "pattern." This aligns with the structured approach of systemd unit files, Dockerfiles/Quadlets for Podman, and Caddyfiles, representing the defined "forms" for deploying and managing applications

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
