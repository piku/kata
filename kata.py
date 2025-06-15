#!/usr/bin/env python3

"Kata Micro-PaaS - Piku refactor"

try:
    from sys import version_info
    assert version_info >= (3, 12)
except AssertionError:
    exit("Kata requires Python 3.12 or above")

from click import argument, Path, echo as click_echo, group, option, UNPROCESSED
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
from subprocess import call, check_output, Popen, STDOUT, run
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
PUID = getuid()
PGID = getgid()
DOCKER_COMPOSE = ".docker-compose.yaml"
KATA_COMPOSE = "kata-compose.yaml"
ROOT_FOLDERS = ['APP_ROOT', 'DATA_ROOT', 'ENV_ROOT', 'CONFIG_ROOT', 'GIT_ROOT', 'LOG_ROOT']
if KATA_BIN not in environ['PATH']:
    environ['PATH'] = KATA_BIN + ":" + environ['PATH']

# === Make sure we can access kata user-installed binaries === #

PYTHON_DOCKERFILE = """
FROM debian:trixie-slim
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
WORKDIR /app
CMD ['python', '-m', 'app']
"""

NODEJS_DOCKERFILE = """
FROM debian:trixie-slim
ARG DEBIAN_FRONTEND=noninteractive
RUN apt update \
 && apt dist-upgrade -y \
 && apt-get -qq install \
    nodejs \
    npm \
    yarnpkg
ENV NODE_PATH=/venv
ENV NPM_CONFIG_PREFIX=/venv
ENV PATH=/venv/bin:/venv/.bin:$PATH
VOLUME ["/app", "/config", "/data", "/venv"]
WORKDIR /app
CMD ['node', 'app.js']
"""

RUNTIME_IMAGES = {
    'kata/python': PYTHON_DOCKERFILE,
    'kata/nodejs': NODEJS_DOCKERFILE
}

# === Utility functions ===

def echo(message, fg=None, nl=True, err=False) -> None:
    """Print a message with optional color"""
    click_echo(message, color=True if fg else None, nl=nl, err=err)


def base_env(app, env=None) -> dict:
    """Get the environment variables for an app"""
    base = {'PGID': str(PGID), 'PUID': str(PUID)}
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

    # finally, an ENV or .env file in the config directory overrides things
    # TODO: validate if this still makes sense
    for name in ['ENV', '.env']:
        env_file = join(CONFIG_ROOT, app, name)
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

# === Docker Helpers ===

def docker_check_image_exists(image_name):
    """Check if a Docker image exists locally"""
    output = check_output(['docker', 'image', 'list', '--format', '{{.Repository}}:{{.Tag}}'], stderr=STDOUT, universal_newlines=True)
    if image_name in output:
        return True
    return False


def docker_create_runtime_image(image_name, dockerfile_content):
    """Create a Docker image from a Dockerfile content"""
    try:
        with NamedTemporaryFile(delete=False, mode='w', suffix='.Dockerfile') as dockerfile:
            dockerfile.write(dockerfile_content)
            dockerfile_path = dockerfile.name
        output = check_output(['docker', 'build', '-t', image_name, '-f', dockerfile_path, '.'], stderr=STDOUT, universal_newlines=True)
        echo(f"Created '{image_name}' successfully.", fg='green')
        return True
    except Exception as e:
        echo(f"Error creating image: {str(e)}", fg='red')
        return False
    finally:
        remove(dockerfile_path)


def docker_handle_runtime_environment(app_name, runtime, destroy=False, env=None):
    image = f"kata/{runtime}"
    if not docker_check_image_exists(image) and not destroy:
        if not docker_create_runtime_image(image, RUNTIME_IMAGES[image]):
            exit(1)
    volumes = [
        "-v", f"{join(APP_ROOT, app_name)}:/app",
        "-v", f"{join(CONFIG_ROOT, app_name)}:/config",
        "-v", f"{join(DATA_ROOT, app_name)}:/data",
        "-v", f"{join(ENV_ROOT, app_name)}:/venv"
    ]
    if destroy:
        cmds = {
            'python': [['chown', '-hR', f'{PUID}:{PGID}', '/data'], 
                       ['chown', '-hR', f'{PUID}:{PGID}', '/venv'], 
                       ['chown', '-hR', f'{PUID}:{PGID}', '/config']],
            'nodejs': [['chown', '-hR', f'{PUID}:{PGID}', '/data'], 
                       ['chown', '-hR', f'{PUID}:{PGID}', '/app'], 
                       ['chown', '-hR', f'{PUID}:{PGID}', '/venv'], 
                       ['chown', '-hR', f'{PUID}:{PGID}', '/config']]
        }
    else:
        cmds = {
            'python': [['python3', '-m', 'venv', '/venv'],
                       ['pip3', 'install', '-r', '/app/requirements.txt']],
            'nodejs': [['npm', 'install' ]]
        }
    for cmd in cmds[runtime]:
        call(['docker', 'run', '--rm'] + volumes + ['-i', f'kata/{runtime}'] + cmd,
         cwd=join(APP_ROOT, app_name), env=env, stdout=stdout, stderr=stderr, universal_newlines=True)

# === App Management ===

def exit_if_invalid(app, deployed=False):
    """Make sure the app exists"""
    app = sanitize_app_name(app)
    app_path = join(APP_ROOT, app)
    if not exists(app_path):
        echo(f"Error: app '{app}' not deployed!", fg='red')
        exit(1)
    return app


def sanitize_app_name(app) -> str:
    """Sanitize the app name"""
    if app:
        return sub(r'[^a-zA-Z0-9_-]', '', app)
    return app


def parse_compose(app_name, filename) -> tuple:
    """Parses the kata-compose.yaml"""

    data = load_yaml(filename, base_env(app_name))

    if not data:
        return None, None

    env = {}
    if "environment" in data:
        env = {k: str(v) for k, v in data["environment"].items()}

    env = base_env(app_name, env)
    env_dump = [f"{k}={v}" for k, v in env.items()]

    #echo(f"Using environment for {app_name}: {",".join(env_dump)}", fg='green')
    if not "services" in data:
        echo(f"Warning: no 'services' section found in {filename}", fg='yellow')
    services = data.get("services", {})

    for service_name, service in services.items():
        echo(f"-----> Preparing service '{service_name}'", fg='green')
        if not "image" in service:
            if "runtime" in service:
                service["image"] = f"kata/{service["runtime"]}"
                echo(f"=====> '{service_name}' will use runtime '{service['runtime']}'", fg='green')
                if service["image"] in RUNTIME_IMAGES:
                    docker_handle_runtime_environment(app_name, service["runtime"], env=env)
                else:
                    echo(f"Error: runtime '{service['runtime']}' not supported", fg='red')
                    exit(1)
                del service["runtime"]
            if not "volumes" in service:
                service["volumes"] = ["app:/app", "config:/config", "data:/data", "venv:/venv"]
            else:
                echo(f"Warning: service '{service_name}' has custom volumes, ensure they are correct", fg='yellow')
        if not "command" in service:
            echo(f"Warning: service '{service_name}' has no 'command' specified", fg='yellow')
            continue
        if not "ports" in service:
            echo(f"Warning: service '{service_name}' has no 'ports' specified", fg='yellow')
        if not "environment" in service:
            service["environment"] = []
        service["environment"].extend(env_dump)

    caddy_config = {}
    if "caddy" in data.keys():
        caddy_config = data.get("caddy", {})
        del data["caddy"]
    else:
        echo(f"Warning: no 'caddy' section found, no HTTP/S handling done.", fg='yellow')

    if not "volumes" in data.keys():
        volumes = {
            "app": join(APP_ROOT, app_name),
            "config": join(CONFIG_ROOT, app_name),
            "data": join(DATA_ROOT, app_name),
            "venv": join(ENV_ROOT, app_name)
        }
        for volume in ["app", "config", "data", "venv"]:
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
    else:
        echo(f"Warning: using app-specific volume setup.", fg='yellow')
    
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
        compose, caddy = parse_compose(app, compose_file)
        if not compose:
            echo(f"Error: could not parse {compose_file}", fg='red')
            return
        with open(join(APP_ROOT, app, DOCKER_COMPOSE), "w", encoding='utf-8') as f:
            f.write(safe_dump(compose))
        if caddy:
            caddy_config(app, caddy)
        do_start(app)
    else:
        echo(f"Error: app '{app}' not found.", fg='red')


def do_start(app):
    app_path = join(APP_ROOT, app)
    if exists(join(app_path, DOCKER_COMPOSE)):
        echo(f"-----> Starting app '{app}'", fg='yellow')
        # Stop the app using docker-compose
        call(['docker', 'stack', 'deploy', app, f'--compose-file={join(app_path, DOCKER_COMPOSE)}', '--detach=true', '--resolve-image=never', '--prune'],
             cwd=app_path, stdout=stdout, stderr=stderr, universal_newlines=True)


def do_stop(app):
    app_path = join(APP_ROOT, app)
    if exists(join(app_path, DOCKER_COMPOSE)):
        echo(f"-----> Stopping app '{app}'", fg='yellow')
        # Stop the app using docker-compose
        call(['docker', 'stack', 'rm', app],
             cwd=app_path, stdout=stdout, stderr=stderr, universal_newlines=True)


def do_remove(app):
    app_path = join(APP_ROOT, app)
    if exists(join(app_path, DOCKER_COMPOSE)):
        echo(f"-----> Removing '{app}'", fg='yellow')
        call(['docker', 'stack', 'rm', app],
             cwd=app_path, stdout=stdout, stderr=stderr, universal_newlines=True)
        yaml = safe_load(open(join(app_path, KATA_COMPOSE), 'r', encoding='utf-8').read())
        if 'services' in yaml:
            for service_name, service in yaml['services'].items():
                echo("---> Removing service: " + service_name, fg='yellow')
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

@command('ls')
def cmd_apps():
    """List apps/stacks"""
    apps = listdir(APP_ROOT)
    if not apps:
        return

    containers = check_output(['docker', 'ps', '--format', '{{.Names}}'], universal_newlines=True).splitlines()
    for a in apps:
        running = False
        for c in containers:
            if c.startswith(a + '-'):
                running = True
                break
        echo(('*' if running else ' ') + a, fg='green')


@command('config:stack')
@argument('app')
def cmd_config(app):
    """Show configuration for an app"""
    app = exit_if_invalid(app)

    config_file = join(APP_ROOT, app, KATA_COMPOSE)
    if exists(config_file):
        echo(open(config_file).read().strip(), fg='white')
    else:
        echo(f"Warning: app '{app}' not deployed, no config found.", fg='yellow')


@command('secrets:set')
@argument('secrets', nargs=-1, required=True)
def cmd_secrets_set(secrets):
    """Set a docker secret: name=value or provide name and value via stdin (multiline supported)"""
    if not secrets:
        k = input("Secret name: ")
        echo("Enter secret value (end with EOF / Ctrl-D):", fg='yellow')
        v = sys.stdin.read().strip()
        secrets = [f"{k}={v}"]
    for s in secrets:
        try:
            k, v = s.split('=', 1)
            echo(f"Setting {k}", fg='white')
            run(['docker', 'secret', 'create', k, '-'], input=v,
                 stdout=stdout, stderr=stderr, universal_newlines=True)
        except Exception as e:
            echo(f"Error {e} for secret '{k}'", fg='red')
            continue


@command('secrets:rm')
@argument('secret', required=True)
def cmd_secrets_rm(secret):
    """Remove a secret"""
    call(['docker', 'secret', 'rm', secret], stdout=stdout, stderr=stderr, universal_newlines=True)


@command('secrets:ls')
def cmd_secrets_ls():
    """List docker secrets defined in host."""
    call(['docker', 'secret', 'ls'], stdout=stdout, stderr=stderr, universal_newlines=True)


@command('config:docker')
@argument('app')
def cmd_config_live(app):
    """Show live config for running app"""
    app = exit_if_invalid(app)
    config_file = join(APP_ROOT, app, DOCKER_COMPOSE)
    if exists(config_file):
        echo(open(config_file).read().strip(), fg='white')
    else:
        echo(f"Warning: app '{app}' not deployed, no config found.", fg='yellow')


@command('config:caddy')
@argument('app')
def cmd_caddy_app(app):
    """Show Caddy configuration for an app"""
    app = exit_if_invalid(app)
    caddy_json = caddy_get(app)
    if caddy_json:
        echo(dumps(caddy_json, indent=2), fg='white')
    else:
        echo(f"Warning: app '{app}' has no Caddy config.", fg='yellow')


@command('rm')
@argument('app')
@option('--force', '-f', is_flag=True, help='Force destruction without confirmation')
@option('--wipe',  '-w', is_flag=True, help='Delete data and config directories')
def cmd_destroy(app, force, wipe):
    """Remove an app"""
    app = sanitize_app_name(app)
    app_path = join(APP_ROOT, app)
    if not exists(app_path):
        echo(f"Error: stack '{app}' not deployed!", fg='red')
        return

    if not force:
        response = input(f"Are you sure you want to destroy '{app}'? [y/N] ")
        if response.lower() != 'y':
            echo("Aborted.", fg='yellow')
            return

    caddy_remove(app)
    do_remove(app)

    paths = [join(APP_ROOT, app), join(ENV_ROOT, app), join(LOG_ROOT, app), join(GIT_ROOT, app)]
    if wipe:
        paths.extend([join(DATA_ROOT, app), join(CONFIG_ROOT, app)])

    for path in paths:
        if exists(path):
            try:
                rmtree(path)
            except Exception as e:
                echo(f"Error removing {path}: {str(e)}", fg='red')
    echo(f"-----> '{app}' destroyed", fg='green')
    if not wipe:
        echo("Data and config directories were not deleted. Use --wipe to remove them.", fg='yellow')

@command('docker', add_help_option=False, context_settings=dict(ignore_unknown_options=True))
@argument('args', nargs=-1, required=True, type=UNPROCESSED)
def cmd_ps(args):
    """Pass-through Docker commands (logs, etc.)"""
    call(['docker'] + list(args),
         stdout=stdout, stderr=stderr, universal_newlines=True)


@command('docker:services')
@argument('stack', required=True)
def cmd_services(stack):
    """List services for a stack"""
    call(['docker', 'stack', 'services', stack],
         stdout=stdout, stderr=stderr, universal_newlines=True)


@command('ps')
@argument('service', nargs=-1, required=True)
def cmd_ps(service):
    """List processes for a service"""
    call(['docker', 'service', 'ps', service],
         stdout=stdout, stderr=stderr, universal_newlines=True)


@command('run')
@argument('service', required=True)
@argument('command', nargs=-1, required=True)
def cmd_run(service, command):
    """Run a command inside a service"""
    app = exit_if_invalid(app)
    call(['docker', 'exec', '-ti', service] + list(command),
         stdout=stdout, stderr=stderr, universal_newlines=True)


@command('restart')
@argument('app')
def cmd_restart(app):
    """Restart an app"""
    app = exit_if_invalid(app)
    do_restart(app)


@command('stop')
@argument('app')
def cmd_stop(app):
    """Stop an app"""
    app = exit_if_invalid(app)
    do_stop(app)


@command('setup')
def cmd_setup():
    """Setup the local kata environment"""
    for f in ROOT_FOLDERS:
        d = globals()[f]
        if not exists(d):
            makedirs(d)
            echo(f"Created {d}", fg='green')
    echo("Kata setup complete", fg='green')


@command("setup:ssh")
@argument('public_key_file')
def cmd_setup_ssh(public_key_file):
    """Set up a new SSH key (use - for stdin)"""
    def add_helper(key_file):
        if exists(key_file):
            try:
                fingerprint = str(check_output('ssh-keygen -lf ' + key_file, shell=True)).split(' ', 4)[1]
                key = open(key_file, 'r').read().strip()
                echo("Adding key '{}'.".format(fingerprint), fg='white')
                setup_authorized_keys(fingerprint, KATA_SCRIPT, key)
            except Exception:
                echo("Error: invalid public key file '{}': {}".format(key_file, format_exc()), fg='red')
        elif public_key_file == '-':
            buffer = "".join(stdin.readlines())
            with NamedTemporaryFile(mode="w") as f:
                f.write(buffer)
                f.flush()
                add_helper(f.name)
        else:
            echo("Error: public key file '{}' not found.".format(key_file), fg='red')

    add_helper(public_key_file)


@command('update')
def cmd_update():
    """Update kata to the latest version"""
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

# === Internal commands ===

@command("git-hook", hidden=True)
@argument('app')
def cmd_git_hook(app):
    # INTERNAL: Post-receive git hook
    app = sanitize_app_name(app)
    repo_path = join(GIT_ROOT, app)
    app_path = join(APP_ROOT, app)
    data_path = join(DATA_ROOT, app)

    for line in stdin:
        oldrev, newrev, refname = line.strip().split(" ")
        if not exists(app_path):
            echo("-----> Creating app '{}'".format(app), fg='green')
            makedirs(app_path)
            if not exists(data_path):
                makedirs(data_path)
            call("git clone --quiet {} {}".format(repo_path, app), cwd=APP_ROOT, shell=True)
        do_deploy(app, newrev=newrev)


@command("git-receive-pack", hidden=True)
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


@command("git-upload-pack", hidden=True)
@argument('app')
def cmd_git_upload_pack(app):
    # INTERNAL: Handle git upload pack for an app
    app = sanitize_app_name(app)
    env = globals()
    env.update(locals())
    # Handle the actual receive. We'll be called with 'git-hook' after it happens
    call('git-shell -c "{}" '.format(argv[1] + " '{}'".format(app)), cwd=GIT_ROOT, shell=True)


@command("scp", context_settings=dict(ignore_unknown_options=True))
@argument('args', nargs=-1, required=True, type=UNPROCESSED)
def cmd_scp(args):
    """Copy files to/from the server"""
    call(["scp"] + list(args), cwd=abspath(environ['HOME']))


@command("help")
def cmd_help():
    """Display help"""
    show_help()


if __name__ == '__main__':
    # Run the CLI with all registered commands
    cli()
