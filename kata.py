#!/usr/bin/env python3

"Kata Micro-PaaS - Piku refactor"

try:
    from sys import version_info
    assert version_info >= (3, 12)
except AssertionError:
    exit("Kata requires Python 3.12 or above")

from click import argument, Path, echo as click_echo, group, option
from yaml import safe_load, safe_dump
from collections import deque
from fcntl import fcntl, F_SETFL, F_GETFL
from glob import glob
from json import loads, dumps, JSONDecodeError
from http.client import HTTPConnection, HTTPSConnection
from os import chmod, getgid, getuid, symlink, unlink, pathsep, remove, stat, listdir, environ, makedirs, O_NONBLOCK
from os.path import abspath, basename, dirname, exists, getmtime, join, realpath, splitext, isdir
from re import sub, match
from shlex import split as shsplit
from shutil import copyfile, rmtree, which
from socket import socket, AF_INET, SOCK_STREAM
from stat import S_IRUSR, S_IWUSR, S_IXUSR
from subprocess import call, check_output, Popen, STDOUT
from sys import argv, stdin, stdout, stderr, version_info, exit, path as sys_path
from tempfile import NamedTemporaryFile
from time import sleep
from traceback import format_exc


# === Make sure we can access all system and user binaries ===

if 'sbin' not in environ['PATH']:
    environ['PATH'] = "/usr/local/sbin:/usr/sbin:/sbin:" + environ['PATH']
if '.local' not in environ['PATH']:
    environ['PATH'] = environ['HOME'] + "/.local/bin:" + environ['PATH']

# === Globals - all tweakable settings are here ===

KATA_RAW_SOURCE_URL = "https://raw.githubusercontent.com/piku/kata/main/refactor/kata.py"
KATA_ROOT = environ.get('KATA_ROOT', join(environ['HOME']))
KATA_BIN = join(environ['HOME'], 'bin')
KATA_SCRIPT = realpath(__file__)
APP_ROOT = abspath(join(KATA_ROOT, "app"))
DATA_ROOT = abspath(join(KATA_ROOT, "data"))
CONFIG_ROOT = abspath(join(KATA_ROOT, "config"))
GIT_ROOT = abspath(join(KATA_ROOT, "repos"))
LOG_ROOT = abspath(join(KATA_ROOT, "logs"))
ENV_ROOT = abspath(join(KATA_ROOT, "envs"))
DOCKER_COMPOSE = ".docker-compose.yaml"
KATA_COMPOSE = "kata-compose.yaml"

ROOT_FOLDERS = ['APP_ROOT', 'DATA_ROOT', 'ENV_ROOT', 'CONFIG_ROOT', 'GIT_ROOT', 'LOG_ROOT']

# Set XDG_RUNTIME_DIR if not set (needed for systemd --user)
if 'XDG_RUNTIME_DIR' not in environ:
    environ['XDG_RUNTIME_DIR'] = f"/run/user/{getuid()}"

# === Make sure we can access kata user-installed binaries === #

if KATA_BIN not in environ['PATH']:
    environ['PATH'] = KATA_BIN + ":" + environ['PATH']

PYTHON_DOCKERFILE = """
FROM debian:trixie
ARG DEBIAN_FRONTEND=noninteractive
RUN apt update \
 && apt dist-upgrade -y \
 && apt-get -qq install \
    python3-pip \
    python3-dev \
    python3-venv

ENV VIRTUAL_ENV=/venv
ENV PATH=/venv/bin:$PATH
VOLUME ["/app", "/config", "/data", "/venv"]
EXPOSE 8080
WORKDIR /app
CMD ['python', '-m', 'app']
"""

RUNTIME_IMAGES = {
    'kata/python': PYTHON_DOCKERFILE,
}

COMPOSE_TEMPLATE = """
services:
    {service_name}:
        image: {image}
        ports:
        - "{port}:{port}"
        environment:
        - PORT={port}
        volumes:
        - app:/app
        - data:/data
        command: {command}
volumes:
    app:
        driver: local
        path: {app_path}
    data:
        driver: local
        path: {data_path}
"""

# Helper functions for click
def echo(message, fg=None, nl=True, err=False):
    """Print a message with optional color"""
    click_echo(message, color=True if fg else None, nl=nl, err=err)

# === Utility functions ===

def get_boolean(value):
    """Convert a boolean-ish string to a boolean."""
    return value.lower() in ['1', 'on', 'true', 'enabled', 'yes', 'y']

def base_env(app, env=None):
    """Get the environment variables for an app"""

    base = {}
    for key in ROOT_FOLDERS:
        try:
            path_value = globals()[key]
            base[key] = join(path_value, app)
        except KeyError:
            echo(f"Error: {key} not found in global variables", fg='red')
            exit(1)

    # If env is provided, update the base environment with it
    if env is not None:
        base.update(env)

    # finally, an ENV or .env file in the app directory overrides things

    for name in ['ENV', '.env']:
        env_file = join(APP_ROOT, app, name)
        if exists(env_file):
            with open(env_file, 'r', encoding='utf-8') as f:
                base.update(dict(line.strip().split('=', 1) for line in f if '=' in line))
    return base

def expandvars(buffer, env, default=None, skip_escaped=False):
    """expand shell-style environment variables in a buffer"""
    def replace_var(match):
        return env.get(match.group(2) or match.group(1), match.group(0) if default is None else default)

    pattern = (r'(?<!\\)' if skip_escaped else '') + r'\$(\w+|\{([^}]*)\})'
    return sub(pattern, replace_var, buffer)

def load_yaml(filename, env=None):
    if not exists(filename):
        echo(f"File not found: {filename}", fg='red')
        return None
    if env is None:
        env = environ.copy()
    with open(filename, 'r', encoding='utf-8') as f:
        content = f.read()
    content = expandvars(content, env)
    try:
        return safe_load(content)
    except Exception as e:
        echo(f"Error parsing YAML: {str(e)}", fg='red')
        return None

def get_free_port(address=""):
    """Find a free TCP port (entirely at random)"""
    s = socket(AF_INET, SOCK_STREAM)
    s.bind((address, 0))
    port = s.getsockname()[1]
    s.close()
    return port

# === SSH and git Helpers ===

def setup_authorized_keys(ssh_fingerprint, script_path, pubkey):
    """Sets up an authorized_keys file to redirect SSH commands"""
    authorized_keys = join(environ['HOME'], '.ssh', 'authorized_keys')
    if not exists(dirname(authorized_keys)):
        makedirs(dirname(authorized_keys))
    # Restrict features and force all SSH commands to go through our script
    with open(authorized_keys, 'a', encoding='utf-8') as h:
        h.write(f"""command="FINGERPRINT={ssh_fingerprint:s} NAME=default {script_path:s} $SSH_ORIGINAL_COMMAND",no-agent-forwarding,no-user-rc,no-X11-forwarding,no-port-forwarding {pubkey:s}\n""")
    chmod(dirname(authorized_keys), S_IRUSR | S_IWUSR | S_IXUSR)
    chmod(authorized_keys, S_IRUSR | S_IWUSR)

def command_output(cmd):
    """executes a command and grabs its output, if any"""
    try:
        env = environ
        return check_output(cmd, env=env, shell=True, universal_newlines=True)
    except Exception as e:
        return str(e)

# === Docker Helpers ===

def docker_check_image_exists(image_name):
    """Check if a Docker image exists locally"""
    try:
        output = check_output(['docker', 'image', 'inspect', image_name], stderr=STDOUT, universal_newlines=True)
        return True
    except Exception as e:
        if "No such image" in str(e):
            return False
        else:
            echo(f"Error checking image: {str(e)}", fg='red')
            return False

def docker_create_runtime_image(image_name, dockerfile_content):
    """Create a Docker image from a Dockerfile content"""
    try:
        with NamedTemporaryFile(delete=False, mode='w', suffix='.Dockerfile') as dockerfile:
            dockerfile.write(dockerfile_content)
            dockerfile_path = dockerfile.name

        # Build the Docker image
        output = check_output(['docker', 'build', '-t', image_name, '-f', dockerfile_path, '.'], stderr=STDOUT, universal_newlines=True)
        echo(f"Created Docker image '{image_name}' successfully.", fg='green')
        return True
    except Exception as e:
        echo(f"Error creating Docker image: {str(e)}", fg='red')
        return False
    finally:
        remove(dockerfile_path)

def docker_handle_runtime_environment(app_name, runtime, destroy=False, env=None):
    image = f"kata/{runtime}"
    if docker_create_runtime_image(image, RUNTIME_IMAGES[image]):
        echo(f"Created Docker image for runtime '{runtime}'", fg='green')
    volumes = [
        "-v", f"{join(APP_ROOT, app_name)}:/app",
        "-v", f"{join(CONFIG_ROOT, app_name)}:/config",
        "-v", f"{join(DATA_ROOT, app_name)}:/data",
        "-v", f"{join(ENV_ROOT, app_name)}:/venv"
    ]
    if destroy:
        cmds = {
            'python': [['rm', '-rf', '/venv/*']]
        }
    else:
        cmds = {
            'python': [['python3', '-m', 'venv', '/venv'], ['pip3', 'install', '-r', '/app/requirements.txt']]
        }
    ### Mount the required volumes and run the docker image for the runtime environment
    for cmd in cmds[runtime]:
        call(['docker', 'run'] + volumes + ['-i', f'kata/{runtime}'] + cmd,
         cwd=join(APP_ROOT, app_name), env=env, stdout=stdout, stderr=stderr, universal_newlines=True)

def docker_cleanup_runtime_environment(app_name, runtime):
    """Cleans up the Docker environment for an app"""
    app_path = join(APP_ROOT, app_name)
    if exists(app_path):
        echo(f"Cleaning up Docker environment for '{app_name}'", fg='green')
        # Remove the Docker volumes associated with the app
        volumes = ['app', 'config', 'data', 'venv']
        for volume in volumes:
            volume_path = join(app_path, volume)
            if exists(volume_path):
                rmtree(volume_path, ignore_errors=True)
                echo(f"Removed volume '{volume}' for app '{app_name}'", fg='green')
    else:
        echo(f"App '{app_name}' does not exist, skipping cleanup.", fg='yellow')

# === App Management ===

def exit_if_invalid(app, deployed=False):
    """Make sure the app exists"""
    app = sanitize_app_name(app)
    app_path = join(APP_ROOT, app)
    if not exists(app_path):
        echo(f"Error: app '{app}' not deployed!", fg='red')
        exit(1)
    return app

def parse_settings(filename, env={}):
    """Parses a settings file and returns a dict with environment variables"""
    if not exists(filename):
        return {}

    with open(filename, 'r') as settings:
        for line in settings:
            if line[0] == '#' or len(line.strip()) == 0:  # ignore comments and newlines
                continue
            try:
                k, v = map(lambda x: x.strip(), line.split("=", 1))
                env[k] = expandvars(v, env)
            except Exception as e:
                echo(f"Error: malformed setting '{line}', ignoring file: {e}", fg='red')
                return {}
    return env

def sanitize_app_name(app):
    """Sanitize the app name"""
    if app:
        return sub(r'[^a-zA-Z0-9_-]', '', app)
    return app


def parse_yaml(app_name, filename) -> tuple:
    """Parses the kata-compose.yaml file and returns a tuple of
        (list[workers], docker-compose dict, caddy config dict)"""

    data = load_yaml(filename, base_env(app_name))

    if not data:
        return None

    env = {}
    if "environment" in data:
        env = {k: str(v) for k, v in data["environment"].items()}

    env = base_env(app_name, env)
    env_dump = [f"{k}={v}" for k, v in env.items()]
    echo(f"Using environment for {app_name}: {",".join(env_dump)}", fg='green')
    if not "services" in data:
        echo(f"Warning: no 'services' section found in {filename}", fg='yellow')
    services = data.get("services", {})

    for service_name, service in services.items():
        if not "image" in service:
            echo(f"Warning: service '{service_name}' in {filename} has no 'image' specified", fg='yellow')
            if "runtime" in service:
                service["image"] = f"kata/{service["runtime"]}"
                if service["image"] in RUNTIME_IMAGES:
                    docker_handle_runtime_environment(app_name, service["runtime"], env=env)
                else:
                    echo(f"Error: runtime '{service['runtime']}' not supported", fg='red')
                    exit(1)
                del service["runtime"]
            if not "volumes" in service:
                echo(f"Warning: service '{service_name}' in {filename} has no 'volumes' specified", fg='yellow')
                service["volumes"] = ["app:/app", "config:/config", "data:/data", "venv:/venv"]
        if not "command" in service:
            echo(f"Warning: service '{service_name}' in {filename} has no 'command' specified", fg='yellow')
            continue
        if not "ports" in service:
            echo(f"Warning: service '{service_name}' in {filename} has no 'ports' specified", fg='yellow')
            continue

        if not "environment" in service:
            service["environment"] = {}
        service["environment"].update(env)

    caddy_config = {}
    if "caddy" in data.keys():
        caddy_config = data.get("caddy", {})
        del data["caddy"]
    else:
        echo(f"Warning: no 'caddy' section found in {filename}", fg='yellow')

    if not "volumes" in data.keys():
        volumes = {
            "app": join(APP_ROOT, app_name),
            "config": join(CONFIG_ROOT, app_name),
            "data": join(DATA_ROOT, app_name),
            "venv": join(ENV_ROOT, app_name)
        }
        echo(f"Warning: no 'volumes' section found in {filename}, creating default", fg='yellow')
        for volume in ["app", "config", "data", "venv"]:
            echo(f"Warning: no '{volume}' volume found in {filename}, creating default", fg='yellow')
            makedirs(volumes[volume], exist_ok=True)
            data["volumes"] = data.get("volumes", {})
            data["volumes"][volume] = {
                "driver": "local",
                "driver_opts": {
                    "o": "bind",
                    "type": "none",
                    "device": volumes[volume]
                }
            }
    if "environment" in data:
        del data['environment']
    return (data, caddy_config)


# Caddy API Management

def validate_caddy_json(config):
    if not isinstance(config, dict):
        return False, "Configuration must be a JSON object"
    if 'listen' in config and not isinstance(config['listen'], list):
        return False, "'listen' must be an array of strings"

    if 'routes' in config and not isinstance(config['routes'], list):
        return False, "'routes' must be an array of route objects"

    # Check for common missing fields
    if 'routes' not in config and 'handle' not in config:
        return False, "Missing required 'routes' or 'handle' field"

    # Check for common handler errors
    if 'handle' in config and isinstance(config['handle'], list):
        for handler in config['handle']:
            if not isinstance(handler, dict):
                return False, "Each handler must be an object"
            if 'handler' not in handler:
                return False, "Each handler must have a 'handler' field"
    return True, None

def caddy_config(app, config_json):
    """Configure Caddy for an app using the admin API"""

    is_valid, error_message = validate_caddy_json(config_json)
    if not is_valid:
        echo(f"Error in caddy configuration: {error_message}", fg='red')
        return False
    try:
        config_data = dumps(config_json).encode('utf-8')
        echo(f"-----> Configuring Caddy for app '{app}'", fg='green')
        # First, get the current complete Caddy configuration
        try:
            c = HTTPConnection('localhost', 2019, timeout=1)
            c.request('GET', '/config/')
            get_resp = c.getresponse()
            current_config = loads(get_resp.read().decode('utf-8'))
            c.close()

            # Ensure the structure exists
            if 'apps' not in current_config:
                current_config['apps'] = {}
            if 'http' not in current_config['apps']:
                current_config['apps']['http'] = {}
            if 'servers' not in current_config['apps']['http']:
                current_config['apps']['http']['servers'] = {}

            # Update only our app's configuration, preserving everything else
            current_config['apps']['http']['servers'][app] = config_json

            # Convert to JSON and encode
            config_data = dumps(current_config).encode('utf-8')

            # Update the full config
            c = HTTPConnection('localhost', 2019, timeout=1)
            c.request('POST', '/load', body=config_data, headers={'Content-Type': 'application/json'})
            resp = c.getresponse()
            body = resp.read().decode('utf-8', errors='replace')
        except Exception as e:
            echo(f"Error preparing Caddy configuration: {e}", fg='red')
            return False

        if resp.status in (200, 201, 204):
            echo(f"-----> Successfully configured Caddy for app '{app}'", fg='green')
            echo(f"-----> Use 'kata caddy:app {app}' to view the configuration", fg='green')
            echo("-----> Use 'kata caddy' to view the complete Caddy configuration", fg='green')
            return True
        else:
            echo(f"Warning: Caddy API configuration failed: {resp.status} {resp.reason}\n{body}", fg='yellow')
            return False

    except Exception as e:
        echo(f"Error configuring Caddy for app '{app}': {e}", fg='red')
        return False
    finally:
        pass

def caddy_get(app=None):
    """Get Caddy configuration using the admin API"""
    try:
        c = HTTPConnection('localhost', 2019, timeout=1)
        api_path = "/config/"
        c.request('GET', api_path)
        resp = c.getresponse()
        body = resp.read().decode('utf-8', errors='replace')
        if resp.status == 200:
            config = loads(body)
            if app:
                if 'apps' in config and 'http' in config['apps']:
                    if 'servers' in config['apps']['http'] and app in config['apps']['http']['servers']:
                        return config['apps']['http']['servers'][app]
                    else:
                        return None  # App-specific config not found
                else:
                    return None  # Invalid config structure
            else:
                return config  # Return full config
        else:
            echo(f"Error: Caddy API returned status {resp.status} - {resp.reason}", fg='red')
            return None
    except Exception as e:
        echo(f"Error getting Caddy configuration: {e}", fg='red')
        return None

def caddy_remove(app):
    """Remove Caddy configuration for an app using the admin API"""
    try:
        echo(f"-----> Removing Caddy configuration for app '{app}'", fg='yellow')

        # First, get the current complete Caddy configuration
        c = HTTPConnection('localhost', 2019, timeout=1)
        c.request('GET', '/config/')
        resp = c.getresponse()
        current_config = loads(resp.read().decode('utf-8'))
        c.close()

        # Check if the app exists in the configuration
        if ('apps' in current_config and 'http' in current_config['apps'] and
            'servers' in current_config['apps']['http'] and app in current_config['apps']['http']['servers']):

            # Remove the app from the configuration, preserving everything else
            del current_config['apps']['http']['servers'][app]

            config_data = dumps(current_config).encode('utf-8')
            c = HTTPConnection('localhost', 2019, timeout=5)
            c.request('POST', '/load', body=config_data,
                      headers={'Content-Type': 'application/json'})
            resp = c.getresponse()
            resp.read()  # Consume the response body

            if resp.status in (200, 204):
                echo(f"-----> Successfully removed Caddy configuration for app '{app}'", fg='green')
                return True
            else:
                echo(f"Warning: Failed to remove Caddy configuration for app '{app}'", fg='yellow')
                return False
        else:
            echo(f"-----> No configuration found for app '{app}'", fg='yellow')
            return True
    except Exception as e:
        echo(f"Error removing Caddy configuration: {e}", fg='red')
        return False
    finally:
        pass

# Basic deployment functions

def do_deploy(app, deltas={}, newrev=None):
    """Deploy an app by resetting the work directory"""

    app_path = join(APP_ROOT, app)
    compose_file = join(app_path, KATA_COMPOSE)

    env = {'GIT_WORK_DIR': app_path}
    if exists(app_path):
        echo(f"-----> Deploying app '{app}'", fg='green')
        call('git fetch --quiet', cwd=app_path, env=env, shell=True)
        if newrev:
            call(f'git reset --hard {newrev}', cwd=app_path, env=env, shell=True)
        call('git submodule init', cwd=app_path, env=env, shell=True)
        call('git submodule update', cwd=app_path, env=env, shell=True)
        compose, caddy = parse_yaml(app, compose_file)
        with open(join(APP_ROOT, app, DOCKER_COMPOSE), "w", encoding='utf-8') as f:
            f.write(safe_dump(compose))
        caddy_config(app, caddy)
        do_start(app)
    else:
        echo(f"Error: app '{app}' not found.", fg='red')

def do_start(app):
    app_path = join(APP_ROOT, app)
    if exists(join(app_path, DOCKER_COMPOSE)):
        echo(f"Starting app '{app}'", fg='yellow')
        # Stop the app using docker-compose
        call(['docker', 'compose', '-f', join(app_path, DOCKER_COMPOSE), 'up', '-d'],
             cwd=app_path, stdout=stdout, stderr=stderr, universal_newlines=True)


def do_stop(app):
    app_path = join(APP_ROOT, app)
    if exists(join(app_path, DOCKER_COMPOSE)):
        echo(f"Stopping app '{app}'", fg='yellow')
        # Stop the app using docker-compose
        call(['docker', 'compose', '-f', join(app_path, DOCKER_COMPOSE), 'stop'],
             cwd=app_path, stdout=stdout, stderr=stderr, universal_newlines=True)

def do_remove(app):
    app_path = join(APP_ROOT, app)
    if exists(join(app_path, DOCKER_COMPOSE)):
        call(['docker', 'compose', '-f', join(app_path, DOCKER_COMPOSE),
              'down', '--rmi', 'all', '--volumes'],
             cwd=app_path, stdout=stdout, stderr=stderr, universal_newlines=True)
        yaml = safe_load(open(join(app_path, KATA_COMPOSE), 'r', encoding='utf-8').read())
        if 'services' in yaml:
            for service_name, service in yaml['services'].items():
                echo("Removing service: " + service_name, fg='yellow')
                if 'runtime' in service:
                    runtime = service['runtime']
                    docker_handle_runtime_environment(app, runtime, destroy=True)

def do_restart(app):
    """Restarts a deployed app"""
    do_stop(app)
    do_start(app)
    pass

# === CLI Commands ===

@group(context_settings=dict(help_option_names=['-h', '--help']))
def cli():
    """Kata: The other smallest PaaS you've ever seen"""
    pass

command = cli.command

@command('apps')
def cmd_apps():
    """List apps, e.g.: kata apps"""
    apps = listdir(APP_ROOT)
    if not apps:
        echo("There are no applications deployed.")
        return

    for a in apps:
        units = glob(join(SYSTEMD_ROOT, f'{a}*.service'))
        running = len(units) != 0
        echo(('*' if running else ' ') + a, fg='green')


@command('config')
@argument('app')
def cmd_config(app):
    """Show config, e.g.: kata config <app>"""
    app = exit_if_invalid(app)

    config_file = join(ENV_ROOT, app, 'ENV')
    if exists(config_file):
        echo(open(config_file).read().strip(), fg='white')
    else:
        echo(f"Warning: app '{app}' not deployed, no config found.", fg='yellow')


@command('config:get')
@argument('app')
@argument('setting')
def cmd_config_get(app, setting):
    """Get a config setting, e.g.: kata config:get <app> KEY"""
    app = exit_if_invalid(app)

    config_file = join(ENV_ROOT, app, 'ENV')
    if exists(config_file):
        env = parse_settings(config_file)
        if setting in env:
            echo(env[setting], fg='white')
        else:
            echo(f"Error: setting '{setting}' not found", fg='red')
    else:
        echo(f"Warning: app '{app}' not deployed, no config found.", fg='yellow')


# TODO: ensure we keep .env updated

@command('config:set')
@argument('app')
@argument('settings', nargs=-1, required=True)
def cmd_config_set(app, settings):
    """Set config, e.g.: kata config:set <app> KEY=value"""
    app = exit_if_invalid(app)

    config_file = join(ENV_ROOT, app, 'ENV')
    env = {}
    if exists(config_file):
        env = parse_settings(config_file)

    for s in settings:
        try:
            k, v = s.split('=', 1)
            env[k] = v
            echo(f"Setting {k}={v} for '{app}'", fg='white')
        except Exception:
            echo(f"Error: malformed setting '{s}'", fg='red')
            continue

    write_config(config_file, env)
    do_deploy(app)


@command('config:docker')
@argument('app')
def cmd_config_live(app):
    """Show live config for running app, e.g.: kata config:live <app>"""
    app = exit_if_invalid(app)

    app_path = join(APP_ROOT, app)
    echo(f"Docker configuration for app '{app}':", fg='green')
    echo(open(join(app_path, DOCKER_COMPOSE), 'r', encoding='utf-8').read().strip(), fg='white')


@command('config:caddy')
@argument('app', required=False)
def cmd_caddy_app(app):
    """Show Caddy configuration for an app, e.g.: kata config:caddy <app>"""
    app = exit_if_invalid(app)

    app_path = join(APP_ROOT, app)
    caddy_json = caddy_get(app)

    if caddy_json and app:
        echo(f"Caddy configuration for app '{app}':", fg='green')
        echo(dumps(caddy_json, indent=2), fg='white')
    elif caddy_json:
        echo(f"Caddy configuration:", fg='green')
        echo(dumps(caddy_json, indent=2), fg='white')
    else:
        echo(f"No Caddy configuration found", fg='yellow')


@command('destroy')
@argument('app')
@option('--force', '-f', is_flag=True, help='Force destruction without confirmation')
def cmd_destroy(app, force):
    """Destroy an app, e.g.: kata destroy <app>"""
    app = sanitize_app_name(app)
    app_path = join(APP_ROOT, app)

    if not exists(app_path):
        echo(f"Error: app '{app}' not deployed!", fg='red')
        return

    if not force:
        response = input(f"Are you sure you want to destroy '{app}'? [y/N] ")
        if response.lower() != 'y':
            echo("Aborted.", fg='yellow')
            return

    # TODO: Remove Caddy configuration
    do_remove(app)

    # Remove app directories
    for path in [
        join(APP_ROOT, app),
        join(ENV_ROOT, app),
        join(LOG_ROOT, app),
        join(GIT_ROOT, app),
        join(DATA_ROOT, app)
    ]:
        if exists(path):
            try:
                rmtree(path)
                echo(f"Removed {path}", fg='green')
            except Exception as e:
                echo(f"Error removing {path}: {str(e)}", fg='red')

    echo(f"App '{app}' destroyed", fg='green')


@command('logs')
@argument('app')
@option('--follow', '-f', is_flag=True, help='Follow log output')
@option('--service', '-s', 'service', help='Show logs for services matching pattern')
def cmd_logs(app, follow, service):
    """Show logs for an app, e.g.: kata logs <app>"""
    app = exit_if_invalid(app)

    call(['docker', 'compose', '-f', join(APP_ROOT, app, DOCKER_COMPOSE), 'logs',
          '{follow}'.format(follow='-f' if follow else ''),
          '{service}'.format(service=service or '')],
          stdout=stdout, stderr=stderr, universal_newlines=True)

@command('ps')
@argument('app', required=False)
def cmd_ps(app):
    """List processes, e.g.: kata ps [<app>]"""

    app = exit_if_invalid(app)
    call(['docker', 'compose', '-f', join(APP_ROOT, app, DOCKER_COMPOSE), 'ps'],
         stdout=stdout, stderr=stderr, universal_newlines=True)

@command('run')
@argument('app')
@argument('service', required=True)
@argument('command', nargs=-1, required=True)
def cmd_run(app, service, command):
    """Run a command in the app environment, e.g.: kata run <app> <command>"""
    app = exit_if_invalid(app)

    call(['docker', 'compose', '-f', join(APP_ROOT, app, DOCKER_COMPOSE), 'run', service] + list(command),
         stdout=stdout, stderr=stderr, universal_newlines=True)

@command('parse')
@argument('filename', type=Path(exists=True))
def parse_file(filename):
    """Parse a YAML file, e.g.: kata parse <filename>"""
    echo(f"Parsing file: {filename}", fg='green')
    try:
        data, caddy = parse_yaml("sample", filename)
        if data:
            echo("Parsed data:", fg='green')
            echo(safe_dump(data, default_flow_style=False), fg='white')
        else:
            echo("No valid data found in the file", fg='yellow')

        if caddy:
            echo("Parsed Caddy configuration:", fg='green')
            echo(safe_dump(caddy, default_flow_style=False), fg='white')

    except Exception as e:
        echo(f"Error parsing file: {str(e)}", fg='red')

@command('restart')
@argument('app')
def cmd_restart(app):
    """Restart an app, e.g.: kata restart <app>"""
    app = exit_if_invalid(app)
    do_restart(app)


@command('stop')
@argument('app')
def cmd_stop(app):
    """Stop an app, e.g.: kata stop <app>"""
    app = exit_if_invalid(app)
    do_stop(app)


@command('setup')
def cmd_setup():
    """Setup kata environment, e.g.: kata setup"""
    for f in ROOT_FOLDERS:
        d = globals()[f]
        if not exists(d):
            makedirs(d)
            echo(f"Created {d}", fg='green')
    echo("Kata setup complete", fg='green')


@command('setup:ssh')
@argument('pubkey', required=False)
def cmd_setup_ssh(pubkey):
    """Setup SSH keys, e.g.: kata setup:ssh [pubkey]"""
    if not pubkey:
        # Look for an existing public key
        default_key = join(environ['HOME'], '.ssh', 'id_rsa.pub')
        if exists(default_key):
            with open(default_key, 'r', encoding='utf-8') as f:
                pubkey = f.read().strip()
        else:
            echo("No public key provided and no default key found", fg='red')
            echo("Generate one with: ssh-keygen -t rsa", fg='yellow')
            return
    try:
        fingerprint = check_output(['ssh-keygen', '-lf', '-'],
                                  input=pubkey.encode('utf-8'),
                                  stderr=STDOUT,
                                  universal_newlines=True).split()[1]
    except Exception as e:
        echo(f"Invalid public key format ({str(e)})", fg='red')
        return
    try:
        setup_authorized_keys(fingerprint, KATA_SCRIPT, pubkey)
        echo("SSH key setup complete", fg='green')
    except Exception as e:
        echo(f"Error setting up SSH keys: {str(e)}", fg='red')


@command('update')
def cmd_update():
    """Update kata to the latest version, e.g.: kata update"""
    try:
        # Download the latest version
        echo("Downloading latest version...", fg='green')
        response = HTTPSConnection('raw.githubusercontent.com').request('GET', KATA_RAW_SOURCE_URL)

        if response.status_code == 200:
            # Create a backup of the current script
            backup_file = f"{KATA_SCRIPT}.backup"
            copyfile(KATA_SCRIPT, backup_file)
            echo(f"Created backup at {backup_file}", fg='green')

            # Write the new version
            with open(KATA_SCRIPT, 'w', encoding='utf-8') as f:
                f.write(response.text)

            # Make it executable
            chmod(KATA_SCRIPT, S_IRUSR | S_IWUSR | S_IXUSR)

            echo("Update complete! Restart any running kata processes.", fg='green')
        else:
            echo(f"Failed to download update: HTTP {response.status_code}", fg='red')
    except ImportError:
        echo("Error: requests module not installed", fg='red')
        echo("Install it with: pip install requests", fg='yellow')
    except Exception as e:
        echo(f"Error updating kata: {str(e)}", fg='red')

# --- Internal commands ---

@command("git-hook")
@argument('app')
def cmd_git_hook(app):
    # INTERNAL: Post-receive git hook

    app = sanitize_app_name(app)
    repo_path = join(GIT_ROOT, app)
    app_path = join(APP_ROOT, app)
    data_path = join(DATA_ROOT, app)

    for line in stdin:
        # pylint: disable=unused-variable
        oldrev, newrev, refname = line.strip().split(" ")
        # Handle pushes
        if not exists(app_path):
            echo("-----> Creating app '{}'".format(app), fg='green')
            makedirs(app_path)
            # The data directory may already exist, since this may be a full redeployment (we never delete data since it may be expensive to recreate)
            if not exists(data_path):
                makedirs(data_path)
            call("git clone --quiet {} {}".format(repo_path, app), cwd=APP_ROOT, shell=True)
        do_deploy(app, newrev=newrev)


@command("git-receive-pack")
@argument('app')
def cmd_git_receive_pack(app):
    # INTERNAL: Handle git pushes for an app

    app = sanitize_app_name(app)
    hook_path = join(GIT_ROOT, app, 'hooks', 'post-receive')
    env = globals()
    env.update(locals())

    if not exists(hook_path):
        makedirs(dirname(hook_path))
        # Initialize the repository with a hook to this script
        call("git init --quiet --bare " + app, cwd=GIT_ROOT, shell=True)
        with open(hook_path, 'w', encoding='utf-8') as h:
            h.write("""#!/usr/bin/env bash
set -e; set -o pipefail;
cat | KATA_ROOT="{KATA_ROOT:s}" {KATA_SCRIPT:s} git-hook {app:s}""".format(**env))
        # Make the hook executable by our user
        chmod(hook_path, stat(hook_path).st_mode | S_IXUSR)
    # Handle the actual receive. We'll be called with 'git-hook' after it happens
    call('git-shell -c "{}" '.format(argv[1] + " '{}'".format(app)), cwd=GIT_ROOT, shell=True)

@command("git-upload-pack")
@argument('app')
def cmd_git_upload_pack(app):
    # INTERNAL: Handle git upload pack for an app
    app = sanitize_app_name(app)
    env = globals()
    env.update(locals())
    # Handle the actual receive. We'll be called with 'git-hook' after it happens
    call('git-shell -c "{}" '.format(argv[1] + " '{}'".format(app)), cwd=GIT_ROOT, shell=True)

@command("scp")
@argument('args', nargs=-1, required=True)
def cmd_scp(args):
    """Simple wrapper to allow scp to work."""
    call(["scp"] + list(args), cwd=abspath(environ['HOME']))

@command("help")
def cmd_help():
    """display help for kata"""
    show_help()

if __name__ == '__main__':
    # Run the CLI with all registered commands
    cli()
