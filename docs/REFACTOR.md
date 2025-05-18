# Piku Refactor - Kata

This is a fork of the original `piku` project, which is a simple and lightweight platform-as-a-service (PaaS) solution for deploying web applications.

The refactor aims to modernize the codebase and improve its maintainability while retaining the core functionality and features of the original project, but with a focus on using `systemd` and `podman` for application management and deployment.

The name change to `kata` does not imply a complete overhaul of the original project but rather an iterative improvement of the existing codebase. The goal is to enhance the user experience and streamline the deployment process while keeping the original spirit of `piku` intact (as well as the original project until/if this is merged back).

The refactor also aims to simplify the codebase by removing unnecessary dependencies and improving the overall structure.

## Goals

* Switch from `nginx` to `caddy`
* Switch from `uwsgi` to `systemd` and `podman` to run apps
* Support only a subset of the runtimes (Python, others via `podman` in a first step)
  * use `systemd --user` and `podman` quadlets to support other runtimes using pre-defined base images
* Build and launch generic containers if a `Dockerfile` or `docker-compose.yaml`  is present
* Remove the dependency on `click` by replacing it with a similar decorator-based approach based on internal decorators written as functions

## Things that should be retained

* The same coding style and structure
* The SSH deployment and monitoring mechanism
* The ability to serve static files
* The ability to set up HTTPS automatically
* The ability to enable Cloudflare-only inbound IP filtering

## Implemented Features Checklist

### Core Technology Switches

* [x] Switch from `nginx` to `caddy`
* [x] Switch from `uWSGI` to `systemd` to run apps
* [x] Switch from `uWSGI` to `podman` to run containerized apps

### Runtime Support

* [x] Support Python runtimes (but only `pip` and `uv`)
* [x] Support generic containers via `Dockerfile`
* [x] Support `docker-compose.yaml` via `podman-compose` (initial support)
* [x] Use `systemd --user` and `podman` quadlets for other runtimes 

### CLI

* [x] Remove the dependency on `click` by implementing a custom decorator-based approach

### Retained Features

* [x] The same coding style and structure
* [x] The SSH deployment and monitoring mechanism
* [x] The ability to serve static files (via Caddy)
* [x] The ability to set up HTTPS automatically (via Caddy)
* [x] The ability to enable Cloudflare-only inbound IP filtering (via Caddy)
