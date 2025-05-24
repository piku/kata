#!/usr/bin/env python3

"Kata Micro-PaaS - Piku refactor"

try:
    from sys import version_info
    assert version_info >= (3, 12)
except AssertionError:
    exit("Kata requires Python 3.12 or above")

from collections import deque
from fcntl import fcntl, F_SETFL, F_GETFL
from glob import glob
from json import loads, dumps, JSONDecodeError
import http.client
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
KATA_ROOT = environ.get('KATA_ROOT', join(environ['HOME'], '.kata'))
KATA_BIN = join(environ['HOME'], 'bin')
KATA_SCRIPT = realpath(__file__)
KATA_PLUGIN_ROOT = abspath(join(KATA_ROOT, "plugins"))
APP_ROOT = abspath(join(KATA_ROOT, "apps"))
DATA_ROOT = abspath(join(KATA_ROOT, "data"))
ENV_ROOT = abspath(join(KATA_ROOT, "envs"))
GIT_ROOT = abspath(join(KATA_ROOT, "repos"))
LOG_ROOT = abspath(join(KATA_ROOT, "logs"))
CADDY_ROOT = abspath(join(KATA_ROOT, "caddy"))
CACHE_ROOT = abspath(join(KATA_ROOT, "cache"))
PODMAN_ROOT = abspath(join(KATA_ROOT, "podman"))
ACME_ROOT = environ.get('ACME_ROOT', join(environ['HOME'], '.acme.sh'))
ACME_WWW = abspath(join(KATA_ROOT, "acme"))
ACME_ROOT_CA = environ.get('ACME_ROOT_CA', 'letsencrypt.org')
UNIT_PATTERN = "%s@%s.service"
SYSTEMD_ROOT = join(environ['HOME'], '.config', 'systemd', 'user')
QUADLET_ROOT = join(SYSTEMD_ROOT, 'containers', 'systemd')

# Set XDG_RUNTIME_DIR if not set (needed for systemd --user)
if 'XDG_RUNTIME_DIR' not in environ:
    environ['XDG_RUNTIME_DIR'] = f"/run/user/{getuid()}"

# === Make sure we can access kata user-installed binaries === #

if KATA_BIN not in environ['PATH']:
    environ['PATH'] = KATA_BIN + ":" + environ['PATH']

# Caddy configuration template
CADDYFILE_TEMPLATE = """
{
  debug
  admin localhost:2019
  auto_https off
  email admin@locahost
}

"""
# Systemd unit templates

SYSTEMD_APP_TEMPLATE = """
[Unit]
Description=Kata app: {app_name} - {process_type} {instance}
After=network.target

[Service]
WorkingDirectory={app_path}
Environment="PORT={port}"
{environment_vars}
ExecStart={command}
Restart=always
RestartSec=10
StandardOutput=append:{log_path}
StandardError=append:{log_path}
SyslogIdentifier={app_name}-{process_type}-{instance}

[Install]
WantedBy=default.target
"""

# Podman Quadlet template
QUADLET_CONTAINER_TEMPLATE = """
[Container]
Image={image}
ContainerName={container_name}
PublishPort={host_port}:{container_port}
Volume={app_path}:/app
Volume={data_path}:/data
{extra_volumes}
{extra_environment}
{command_section}

[Service]
StandardOutput=append:{log_path}
StandardError=append:{log_path}
SyslogIdentifier={app_name}-container
Restart=always
RestartSec=10

[Install]
WantedBy=default.target
"""

# Systemd timer template for cron jobs
SYSTEMD_TIMER_TEMPLATE = """
[Unit]
Description=Timer for Kata app: {app_name} - {process_type}

[Timer]
OnCalendar={calendar_spec}
Persistent=true

[Install]
WantedBy=timers.target
"""

# === Simplified CLI decorators ===

# Registry for commands and arguments
_commands = {}
_arguments = {}

def command(name):
    """Register a function as a CLI command"""
    def decorator(f):
        _commands[name] = {
            "name": name,
            "func": f,
            "doc": f.__doc__
        }
        return f
    return decorator

def argument(name, nargs=1):
    """Add an argument to a command"""
    def decorator(f):
        if f.__name__ not in _arguments:
            _arguments[f.__name__] = []

        _arguments[f.__name__].append({
            "name": name,
            "nargs": nargs
        })
        return f

    return decorator

def pass_context(f):
    """Mark a function as requiring context"""
    f._pass_context = True
    return f

def echo(message, fg=None, nl=True, err=False):
    """Print a message with optional color"""
    color_map = {
        'green': '\033[92m',
        'red': '\033[91m',
        'yellow': '\033[93m',
        'white': '\033[97m',
    }
    reset = '\033[0m'

    output_stream = stderr if err else stdout

    if fg and fg in color_map:
        print(f"{color_map[fg]}{message}{reset}", end='\n' if nl else '', file=output_stream, flush=True)
    else:
        print(message, end='\n' if nl else '', file=output_stream, flush=True)

class Context(dict):
    """Simple context dict for commands that need it"""
    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    def get_help(self):
        """Get help information"""
        return "The other smallest PaaS you've ever seen"

def show_help():
    """Show help for all commands"""
    # Get program description
    echo("Kata: The other smallest PaaS you've ever seen", fg="green")

    echo("\nCommands:", fg="green")
    for cmd_name in sorted(_commands.keys()):
        cmd_info = _commands[cmd_name]
        cmd_help = (cmd_info["doc"] or "").split("\n")[0]
        if cmd_help:
            echo(f"  {cmd_name:<15} {cmd_help}", fg="white")
    echo("")

def run_cli():
    """Run the CLI with arguments from sys.argv"""
    args = argv[1:]

    # Show help if no command specified or help requested
    if not args or args[0] in ('-h', '--help'):
        show_help()
        return 0

    cmd_name = args[0]
    cmd_args = args[1:]

    if cmd_name in _commands:
        command_func = _commands[cmd_name]["func"]

        # Parse arguments
        kwargs = {}
        func_name = command_func.__name__
        if func_name in _arguments:
            arg_index = 0
            for arg_meta in _arguments[func_name]:
                arg_name = arg_meta["name"]
                nargs = arg_meta["nargs"]

                if nargs == -1:  # Variable arguments
                    kwargs[arg_name] = cmd_args[arg_index:]
                    break
                elif nargs == 1:  # Single argument
                    if arg_index < len(cmd_args):
                        kwargs[arg_name] = cmd_args[arg_index]
                        arg_index += 1
                else:  # Multiple arguments
                    values = []
                    for _ in range(nargs):
                        if arg_index < len(cmd_args):
                            values.append(cmd_args[arg_index])
                            arg_index += 1
                    kwargs[arg_name] = values

        # Create context for commands that require it
        ctx = Context(command=func_name, args=cmd_args)

        try:
            if hasattr(command_func, '_pass_context'):
                return command_func(ctx, **kwargs) or 0
            else:
                return command_func(**kwargs) or 0
        except Exception as e:
            echo(f"Error: {str(e)}", fg="red", err=True)
            return 1

    echo(f"Error: Command '{cmd_name}' not found", fg="red", err=True)
    show_help()
    return 1

# === Caddy API Management ===

def reload_caddy_admin():
    """Reload Caddy config using the admin API (localhost:2019)"""
    try:
        conn = http.client.HTTPConnection('localhost', 2019, timeout=3)
        conn.request('POST', '/load', headers={'Content-Type': 'application/json'})
        resp = conn.getresponse()
        if resp.status == 200:
            echo("-----> Reloaded Caddy configuration via admin API", fg='green')
        else:
            body = resp.read().decode(errors='replace')
            echo(f"Warning: Caddy admin API reload failed: {resp.status} {resp.reason}\n{body}", fg='yellow')
        conn.close()
    except Exception as e:
        echo(f"Warning: Could not reload Caddy via admin API: {e}", fg='yellow')


def load_caddy_json(app_path):
    """Load the caddy.json file from the app directory if it exists"""
    caddy_json = join(app_path, 'caddy.json')
    if exists(caddy_json):
        try:
            with open(caddy_json, 'r', encoding='utf-8') as f:
                echo("-----> Found caddy.json configuration", fg='green')
                json_content = f.read()
                try:
                    return loads(json_content)
                except Exception as json_error:
                    echo(f"Error parsing caddy.json: {json_error}", fg='red')
                    # Try to identify the location of the error
                    try:
                        loads(json_content)
                    except JSONDecodeError as detailed_error:
                        echo(f"JSON syntax error at line {detailed_error.lineno}, column {detailed_error.colno}: {detailed_error.msg}", fg='yellow')
                    echo("Make sure your caddy.json contains valid JSON", fg='yellow')
                    return None
        except Exception as e:
            echo(f"Error loading caddy.json for app: {e}", fg='red')
            echo("Make sure your caddy.json file exists and is readable", fg='yellow')
    return None

def expand_env_in_json(json_obj, env):
    """Recursively substitute environment variables in JSON values"""
    if isinstance(json_obj, dict):
        return {k: expand_env_in_json(v, env) for k, v in json_obj.items()}
    elif isinstance(json_obj, list):
        return [expand_env_in_json(item, env) for item in json_obj]
    elif isinstance(json_obj, str):
        return expandvars(json_obj, env)
    else:
        return json_obj

def get_worker_types(app):
    """Return list of worker types for an app"""
    app = sanitize_app_name(app)
    app_path = join(APP_ROOT, app)
    procfile = join(app_path, 'Procfile')

    # First check if we parsed workers from procfile
    if exists(procfile):
        workers = parse_procfile(procfile)
        if workers:
            return list(workers.keys())

    # Check for special case workers from container/compose deployments
    workers = []
    if exists(join(app_path, 'Dockerfile')):
        workers.append('container')
    if exists(join(app_path, 'docker-compose.yaml')):
        workers.append('compose')
    if exists(join(app_path, 'caddy.json')) and not workers:
        workers.append('static')
    return workers

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

def configure_caddy_for_app(app, env):
    """Configure Caddy for an app using the admin API"""
    app = sanitize_app_name(app)
    app_path = join(APP_ROOT, app)

    config_json = load_caddy_json(app_path)

    if not config_json:
        echo(f"No caddy.json found for app '{app}', skipping Caddy configuration", fg='yellow')
        echo("Add a caddy.json file to configure web routing for this app", fg='yellow')
        return False

    is_valid, error_message = validate_caddy_json(config_json)
    if not is_valid:
        echo(f"Error in caddy.json: {error_message}", fg='red')
        echo("Please check your caddy.json file for errors", fg='yellow')
        return False

    # TODO: make sure we are handling PORT as an internal affair
    if 'PORT' not in env and not any(worker in ['container', 'compose'] for worker in get_worker_types(app)):
        echo("Error: PORT environment variable must be set for Caddy configuration", fg='red')
        return False
    try:
        config_json = expand_env_in_json(config_json, env)
        config_data = dumps(config_json).encode('utf-8')
        echo(f"-----> Configuring Caddy for app '{app}'", fg='green')

        # Output what we're sending
        echo(str(config_data), fg="yellow")

        # First, get the current complete Caddy configuration
        try:
            get_conn = http.client.HTTPConnection('localhost', 2019, timeout=5)
            get_conn.request('GET', '/config/')
            get_resp = get_conn.getresponse()
            current_config = loads(get_resp.read().decode('utf-8'))
            get_conn.close()

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
            conn = http.client.HTTPConnection('localhost', 2019, timeout=5)
            conn.request('POST', '/load', body=config_data,
                        headers={'Content-Type': 'application/json'})
            resp = conn.getresponse()
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

def get_caddy_config(app=None):
    """Get Caddy configuration using the admin API"""
    try:
        conn = http.client.HTTPConnection('localhost', 2019, timeout=5)
        api_path = "/config/"
        conn.request('GET', api_path)
        resp = conn.getresponse()
        body = resp.read().decode('utf-8', errors='replace')
        if resp.status == 200:
            config = loads(body)
            if app:
                app = sanitize_app_name(app)
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

def remove_caddy_config_for_app(app):
    """Remove Caddy configuration for an app using the admin API"""
    app = sanitize_app_name(app)

    try:
        echo(f"-----> Removing Caddy configuration for app '{app}'", fg='yellow')

        # First, get the current complete Caddy configuration
        conn = http.client.HTTPConnection('localhost', 2019, timeout=5)
        conn.request('GET', '/config/')
        resp = conn.getresponse()
        current_config = loads(resp.read().decode('utf-8'))
        conn.close()

        # Check if the app exists in the configuration
        if ('apps' in current_config and 'http' in current_config['apps'] and
            'servers' in current_config['apps']['http'] and app in current_config['apps']['http']['servers']):

            # Remove the app from the configuration, preserving everything else
            del current_config['apps']['http']['servers'][app]

            config_data = dumps(current_config).encode('utf-8')
            conn = http.client.HTTPConnection('localhost', 2019, timeout=5)
            conn.request('POST', '/load', body=config_data,
                         headers={'Content-Type': 'application/json'})
            resp = conn.getresponse()
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

# === Utility functions ===

def sanitize_app_name(app):
    """Sanitize the app name and build matching path"""
    app = "".join(c for c in app if c.isalnum() or c in ('.', '_', '-')).rstrip().lstrip('/')
    return app


def exit_if_invalid(app):
    """Utility function for error checking upon command startup."""
    app = sanitize_app_name(app)
    if not exists(join(APP_ROOT, app)):
        echo("Error: app '{}' not found.".format(app), fg='red')
        exit(1)
    return app


def get_free_port(address=""):
    """Find a free TCP port (entirely at random)"""
    s = socket(AF_INET, SOCK_STREAM)
    s.bind((address, 0))
    port = s.getsockname()[1]
    s.close()
    return port


def get_boolean(value):
    """Convert a boolean-ish string to a boolean."""
    return value.lower() in ['1', 'on', 'true', 'enabled', 'yes', 'y']


def write_config(filename, bag, separator='='):
    """Helper for writing out config files"""
    with open(filename, 'w', encoding='utf-8') as h:
        for k, v in bag.items():
            h.write(f"{k:s}{separator:s}{v}\n")


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


def parse_procfile(filename):
    """Parses a Procfile and returns the worker types. Only one worker of each type is allowed."""
    workers = {}
    if not exists(filename):
        return {}

    with open(filename, 'r', encoding='utf-8') as procfile:
        for line_number, line in enumerate(procfile):
            line = line.strip()
            if line.startswith("#") or not line:
                continue
            try:
                if ":" not in line:
                    echo(f"Warning: missing colon separator in Procfile at line {line_number + 1}: '{line}'", fg='yellow')
                    continue
                
                kind, cmd = map(lambda x: x.strip(), line.split(":", 1))
                # Warn about deprecated worker types
                if kind == 'wsgi':
                    echo("Warning: 'wsgi' worker type is deprecated. Please use 'web' instead.", fg='yellow')
                    kind = 'web'
                # Check for cron patterns
                if kind.startswith("cron"):
                    limits = [59, 24, 31, 12, 7]
                    res = match(r"^((?:(?:\*\/)?\d+)|\*) ((?:(?:\*\/)?\d+)|\*) ((?:(?:\*\/)?\d+)|\*) ((?:(?:\*\/)?\d+)|\*) ((?:(?:\*\/)?\d+)|\*) (.*)$", cmd)
                    if res:
                        matches = res.groups()
                        for i in range(len(limits)):
                            if int(matches[i].replace("*/", "").replace("*", "1")) > limits[i]:
                                raise ValueError
                workers[kind] = cmd
            except Exception as e:
                echo(f"Warning: misformatted Procfile entry '{line}' at line {line_number + 1}: {e}", fg='yellow')
    if len(workers) == 0:
        return {}
    return workers


def expandvars(buffer, env, default=None, skip_escaped=False):
    """expand shell-style environment variables in a buffer"""
    def replace_var(match):
        return env.get(match.group(2) or match.group(1), match.group(0) if default is None else default)

    pattern = (r'(?<!\\)' if skip_escaped else '') + r'\$(\w+|\{([^}]*)\})'
    return sub(pattern, replace_var, buffer)


def command_output(cmd):
    """executes a command and grabs its output, if any"""
    try:
        env = environ
        result = check_output(cmd, stderr=STDOUT, env=env, shell=True)
        # Properly decode bytes to string
        return result.decode('utf-8', errors='replace')
    except Exception:
        return ""


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


def check_requirements(binaries):
    """Checks if all the binaries exist and are executable"""
    echo(f"-----> Checking requirements: {binaries}", fg='green')
    requirements = list(map(which, binaries))
    echo(str(requirements))

    if None in requirements:
        return False
    return True

# === Application Mangement ===

def found_app(kind):
    """Helper function to output app detected"""
    echo(f"-----> {kind} app detected.", fg='green')
    return True


def do_deploy(app, deltas={}, newrev=None):
    """Deploy an app by resetting the work directory"""

    app_path = join(APP_ROOT, app)
    procfile = join(app_path, 'Procfile')
    caddy_json = join(app_path, 'caddy.json')
    dockerfile_path = join(app_path, 'Dockerfile')
    compose_path = join(app_path, 'docker-compose.yaml')
    log_path = join(LOG_ROOT, app)

    env = {'GIT_WORK_DIR': app_path}
    if exists(app_path):
        echo(f"-----> Deploying app '{app}'", fg='green')
        call('git fetch --quiet', cwd=app_path, env=env, shell=True)
        if newrev:
            call(f'git reset --hard {newrev}', cwd=app_path, env=env, shell=True)
        call('git submodule init', cwd=app_path, env=env, shell=True)
        call('git submodule update', cwd=app_path, env=env, shell=True)
        if not exists(log_path):
            makedirs(log_path)

        workers = parse_procfile(procfile)
        caddy_json = join(app_path, 'caddy.json')

        # Add a virtual 'static' worker if we have caddy.json but no workers from Procfile
        if (not workers or len(workers) == 0) and exists(caddy_json):
            workers = {"static": "echo 'Static site via Caddy'"}

        if workers and len(workers) > 0:
            settings = {}

            # Check for wsgi worker and show a warning
            if "wsgi" in workers:
                echo("Warning: 'wsgi' worker type is deprecated. Please use 'web' instead.", fg='yellow')
                # Ignore wsgi worker
                workers.pop("wsgi", None)

            if "preflight" in workers:
                echo("-----> Running preflight.", fg='green')
                retval = call(workers["preflight"], cwd=app_path, env=settings, shell=True)
                if retval:
                    echo(f"-----> Exiting due to preflight command error value: {retval}")
                    exit(retval)
                workers.pop("preflight", None)

            # Detect application type and deploy
            static_check_keys = set(workers.keys()) - {"preflight", "release"}
            deployed = False
            if exists(join(app_path, 'requirements.txt')) and found_app("Python"):
                settings.update(deploy_python(app, deltas))
                deployed = True
            elif exists(join(app_path, 'pyproject.toml')) and (exists(join(app_path, 'uv.lock')) or exists(join(app_path, '.uv'))) and which('uv') and found_app("Python (uv)"):
                settings.update(deploy_python_with_uv(app, deltas))
                deployed = True
            elif exists(dockerfile_path) and found_app("Containerized") and check_requirements(['podman']):
                settings.update(deploy_containerized(app, deltas))
                deployed = True
            elif exists(compose_path) and found_app("Docker Compose") and check_requirements(['podman-compose']):
                settings.update(deploy_compose(app, deltas))
                deployed = True
            elif (
                # Only static worker(s) present (allow preflight/release)
                len(static_check_keys) > 0 and all(k == "static" for k in static_check_keys)
                and found_app("Static")
            ):
                # Only static worker(s) present
                settings.update(spawn_app(app, deltas))
                deployed = True
            # If static worker is present, always (re)generate Caddy config for static assets
            if 'static' in workers and not deployed:
                settings.update(spawn_app(app, deltas))
                deployed = True
            if not deployed:
                if exists(caddy_json):
                    echo("-----> Detected static site with caddy.json", fg='green')
                    settings.update(spawn_app(app, deltas))
                    deployed = True
                # Check for containerized apps
                elif exists(dockerfile_path) and found_app("Containerized") and check_requirements(['podman']):
                    if exists(caddy_json):
                        echo("-----> Deploying containerized app with web interface via caddy.json", fg='green')
                    else:
                        echo("-----> Deploying containerized app without web interface", fg='green')
                    settings.update(deploy_containerized(app, deltas))
                    deployed = True
                elif exists(compose_path) and found_app("Docker Compose") and check_requirements(['podman-compose']):
                    if exists(caddy_json):
                        echo("-----> Deploying compose app with web interface via caddy.json", fg='green')
                    else:
                        echo("-----> Deploying compose app without web interface", fg='green')
                    settings.update(deploy_compose(app, deltas))
                    deployed = True
                else:
                    echo("-----> Could not detect runtime!", fg='red')
                    echo("-----> Only Python, containerized apps, and static sites with caddy.json are currently supported.", fg='yellow')

            if "release" in workers:
                echo("-----> Releasing", fg='green')
                retval = call(workers["release"], cwd=app_path, env=settings, shell=True)
                if retval:
                    echo("-----> Exiting due to release command error value: {}".format(retval))
                    exit(retval)
                workers.pop("release", None)
        else:
            # Check for container files or caddy.json even without a valid Procfile
            if exists(dockerfile_path) and found_app("Containerized") and check_requirements(['podman']):
                echo("-----> No Procfile found, but Dockerfile exists. Deploying containerized app.", fg='yellow')
                # Create a non-web worker for the container
                workers = {"container": "podman"}
                settings = deploy_containerized(app, deltas)
                # If we also have caddy.json, mention it will be used for web routing
                if exists(caddy_json):
                    echo("-----> Found caddy.json, will configure web routing for containerized app", fg='green')
            elif exists(compose_path) and found_app("Docker Compose") and check_requirements(['podman-compose']):
                echo("-----> No Procfile found, but docker-compose.yaml exists. Deploying with podman-compose.", fg='yellow')
                # Create a non-web worker for the compose setup
                workers = {"compose": "podman-compose"}
                settings = deploy_compose(app, deltas)
                # If we also have caddy.json, mention it will be used for web routing
                if exists(caddy_json):
                    echo("-----> Found caddy.json, will configure web routing for compose app", fg='green')
            # Check if there's a caddy.json file even though Procfile is invalid/empty
            elif exists(caddy_json):
                echo("-----> No valid Procfile found, but caddy.json exists. Deploying as static site.", fg='yellow')
                settings = spawn_app(app, deltas)
            else:
                echo(f"Error: No valid Procfile, Dockerfile, docker-compose.yaml or caddy.json found for app '{app}'.", fg='red')
    else:
        echo(f"Error: app '{app}' not found.", fg='red')


def deploy_python(app, deltas={}):
    """Deploy a Python application"""

    venv_path = join(ENV_ROOT, app)
    requirements = join(APP_ROOT, app, 'requirements.txt')
    env_file = join(APP_ROOT, app, 'ENV')
    # Set unbuffered output and readable UTF-8 mapping
    env = {
        'PYTHONUNBUFFERED': '1',
        'PYTHONIOENCODING': 'UTF_8:replace'
    }
    if exists(env_file):
        env.update(parse_settings(env_file, env))

    first_time = False
    if not exists(join(venv_path, "bin", "activate")):
        echo(f"-----> Creating venv for '{app}'", fg='green')
        try:
            makedirs(venv_path)
        except FileExistsError:
            echo(f"-----> Env dir already exists: '{app}'", fg='yellow')
        # Use python3 explicitly instead of python
        call(f'python3 -m venv {app:s}', cwd=ENV_ROOT, shell=True)
        first_time = True

    # Use environment variable approach instead of activate_this.py
    venv_bin = join(venv_path, 'bin')
    environ['PATH'] = venv_bin + pathsep + environ['PATH']
    environ['VIRTUAL_ENV'] = venv_path
    # Remove PYTHONHOME if it exists as it can interfere with the virtual environment
    if 'PYTHONHOME' in environ:
        del environ['PYTHONHOME']
    
    # Use the pip from the virtual environment
    pip_path = join(venv_bin, 'pip')

    if first_time or getmtime(requirements) > getmtime(venv_path):
        echo(f"-----> Running pip for '{app}'", fg='green')
        call(f'{pip_path} install -r {requirements}', cwd=venv_path, shell=True)
    return spawn_app(app, deltas)


def deploy_python_with_uv(app, deltas={}):
    """Deploy a Python application using Astral uv"""

    echo(f"=====> Starting uv deployment for '{app}'", fg='green')
    env_file = join(APP_ROOT, app, 'ENV')
    venv_path = join(ENV_ROOT, app)
    # Set unbuffered output and readable UTF-8 mapping
    env = {
        **environ,
        'PYTHONUNBUFFERED': '1',
        'PYTHONIOENCODING': 'UTF_8:replace',
        'UV_PROJECT_ENVIRONMENT': venv_path
    }
    if exists(env_file):
        env.update(parse_settings(env_file, env))

    echo("-----> Calling uv sync", fg='green')
    call('uv sync --python-preference only-system', cwd=join(APP_ROOT, app), env=env, shell=True)

    return spawn_app(app, deltas)


def deploy_containerized(app, deltas={}):
    """Deploy a containerized application using Podman Quadlets"""

    app_path = join(APP_ROOT, app)
    env_file = join(APP_ROOT, app, 'ENV')
    env = {}

    if exists(env_file):
        env.update(parse_settings(env_file, env))

    echo(f"-----> Building container for '{app}'", fg='green')
    call(f'podman build -t {app} .', cwd=app_path, shell=True)

    # Ensure quadlet directory exists
    podman_dir = join(PODMAN_ROOT, app)
    if not exists(podman_dir):
        makedirs(podman_dir)

    echo(f"-----> Setting up Podman Quadlet configuration for '{app}'", fg='green')

    return spawn_app(app, deltas)


def deploy_compose(app, deltas={}):
    """Deploy an application using podman-compose"""

    app_path = join(APP_ROOT, app)
    env_file = join(APP_ROOT, app, 'ENV')
    env = {}

    if exists(env_file):
        env.update(parse_settings(env_file, env))

    echo(f"-----> Setting up podman-compose for '{app}'", fg='green')
    return spawn_app(app, deltas)


def spawn_app(app, deltas={}):
    """Create all workers for an app using systemd units and Caddy"""

    app_path = join(APP_ROOT, app)
    procfile = join(app_path, 'Procfile')
    caddy_json = join(app_path, 'caddy.json')
    dockerfile_path = join(app_path, 'Dockerfile')
    compose_path = join(app_path, 'docker-compose.yaml')

    workers = parse_procfile(procfile)
    workers.pop("preflight", None)
    workers.pop("release", None)
    worker_count = {k: 1 for k in workers.keys()}

    # Handle special cases where we don't have a Procfile or it's empty
    if len(workers) == 0:
        if exists(dockerfile_path):
            echo("-----> No Procfile found, but Dockerfile exists. Using containerized deployment.", fg='yellow')
            workers["container"] = "podman"  # Use a non-web worker type
            worker_count["container"] = 1
            # If we also have caddy.json, mention it will be used for web routing
            if exists(caddy_json):
                echo("-----> Found caddy.json, will configure web routing for containerized app", fg='green')
                # Default port will be set in spawn_app
        # For compose apps without Procfile, add a system worker (not web)
        elif exists(compose_path):
            echo("-----> No Procfile found, but docker-compose.yaml exists. Using compose deployment.", fg='yellow')
            workers["compose"] = "podman-compose"  # Use a non-web worker type
            worker_count["compose"] = 1
            # If we also have caddy.json, mention it will be used for web routing
            if exists(caddy_json):
                echo("-----> Found caddy.json, will configure web routing for compose app", fg='green')
                # Default port will be set in spawn_app
        # Add a virtual 'static' worker if we have only caddy.json but no container/compose files
        elif exists(caddy_json):
            echo("-----> No Procfile found, but caddy.json exists. Treating as static site.", fg='yellow')
            workers["static"] = "echo 'Static site via Caddy'"
            worker_count["static"] = 1

    venv_path = join(ENV_ROOT, app)
    env_file = join(APP_ROOT, app, 'ENV')
    settings = join(ENV_ROOT, app, 'ENV')
    live = join(ENV_ROOT, app, 'LIVE_ENV')
    scaling = join(ENV_ROOT, app, 'SCALING')

    # Bootstrap environment
    env = {
        'APP': app,
        'APP_ROOT': join(APP_ROOT, app),
        'LOG_ROOT': join(LOG_ROOT, app),
        'DATA_ROOT': join(DATA_ROOT, app),
        'CACHE_ROOT': join(CACHE_ROOT, app),
        'HOME': environ['HOME'],
        'USER': environ['USER'],
        'PATH': ':'.join([join(venv_path, 'bin'), environ['PATH']]),
        'PWD': dirname(env_file),
        'VIRTUAL_ENV': venv_path,
    }

    safe_defaults = {
        'BIND_ADDRESS': '127.0.0.1',
    }

    # Load environment variables shipped with repo (if any)
    if exists(env_file):
        env.update(parse_settings(env_file, env))

    # Override with custom settings (if any)
    if exists(settings):
        env.update(parse_settings(settings, env))

    # For containerized apps with caddy.json, set a default port if none exists
    if ('container' in workers.keys() or 'compose' in workers.keys()) and exists(caddy_json) and 'PORT' not in env:
        echo("-----> No PORT specified in ENV, using default port 8080 for Caddy configuration", fg='yellow')
        env['PORT'] = '8080'

    # Check whether we need to configure Caddy
    needs_caddy = 'web' in workers or 'static' in workers or exists(caddy_json)

    if needs_caddy:
        echo("-----> Configuring web application", fg='green')

        if 'PORT' not in env and not any(worker_type in ['container', 'compose'] for worker_type in workers.keys()):
            echo("Error: PORT environment variable must be set for web applications", fg='red')
            exit(1)

        for k, v in safe_defaults.items():
            if k not in env:
                env[k] = v

        configure_caddy_for_app(app, env)

        if 'web' in workers:
            app_address = "{BIND_ADDRESS:s}:{PORT:s}".format(**env)
            echo(f"-----> App '{app}' will listen on {app_address}", fg='green')
            env['APP_ADDRESS'] = app_address

    if exists(scaling):
        worker_count.update({k: int(v) for k, v in parse_procfile(scaling).items() if k in workers})

    to_create = {}
    to_destroy = {}
    for k, v in worker_count.items():
        to_create[k] = range(1, worker_count[k] + 1)
        if k in deltas and deltas[k]:
            to_create[k] = range(1, worker_count[k] + deltas[k] + 1)
            if deltas[k] < 0:
                to_destroy[k] = range(worker_count[k], worker_count[k] + deltas[k], -1)
            worker_count[k] = worker_count[k] + deltas[k]

    # Save current settings
    live_dir = dirname(live)
    if not exists(live_dir):
        makedirs(live_dir)
    write_config(live, env)
    write_config(scaling, worker_count, ':')

    # Create new workers
    for k, v in to_create.items():
        if k == "static":
            continue  # Skip static workers for systemd unit/symlink creation
        for w in v:
            unit_name = f"{app}_{k}.{w}"
            echo(f"-----> Spawning '{app}:{k}.{w}'", fg='green')
            final_unit = spawn_worker(app, k, workers[k], env, w)
            
            if final_unit:
                # Get the basename of the unit file for systemctl commands
                unit_name = basename(final_unit)
                
                # Set up environment variables for systemd
                if 'XDG_RUNTIME_DIR' not in environ:
                    environ['XDG_RUNTIME_DIR'] = f"/run/user/{getuid()}"
                
                # Reload systemd to recognize new units
                call('systemctl --user daemon-reload', shell=True)
                
                # Enable and start the unit
                echo(f"-----> Enabling service: {unit_name}", fg='green')
                call(f'systemctl --user enable {unit_name}', shell=True)
                
                echo(f"-----> Starting service: {unit_name}", fg='green')
                call(f'systemctl --user start {unit_name}', shell=True)

    # Remove unnecessary workers
    for k, v in to_destroy.items():
        for w in v:
            unit_name = "{app:s}_{k:s}.{w:d}"
            unit_file = join(SYSTEMD_ROOT, unit_name + '.service')

            if exists(unit_file):
                echo(f"-----> Terminating '{app:s}:{k:s}.{w:d}'", fg='yellow')

                # Stop and disable the service
                call(f'systemctl --user stop {unit_name}', shell=True)
                call(f'systemctl --user disable {unit_name}', shell=True)

                # Check if this is a container file
            is_container = unit_file.endswith('.service') and exists(unit_file.replace('.service', '.container'))

            if is_container:
                container_file = unit_file.replace('.service', '.container')
                quadlet_link = join(environ['HOME'], '.config/systemd/user/containers/systemd', basename(container_file))

                # Remove the quadlet file and symlink
                if exists(quadlet_link):
                    unlink(quadlet_link)
                if exists(container_file):
                    unlink(container_file)
                # Run daemon-reload to update systemd
                call('systemctl --user daemon-reload', shell=True)
            else:
                # Remove the regular symlink and unit file
                if exists(unit_file):
                    unlink(unit_file)

    return env


def spawn_worker(app, kind, command, env, ordinal=1, unit_file=None):
    """Set up and deploy a single worker of a given kind using systemd"""
    
    env['PROC_TYPE'] = kind
    app_path = join(APP_ROOT, app)
    log_file = join(LOG_ROOT, app, "{kind}.{ordinal}.log".format(**locals()))
    data_path = join(DATA_ROOT, app)
    venv_path = join(ENV_ROOT, app)
    
    # Ensure PATH has virtualenv bin directory at the front
    if 'PATH' not in env:
        env['PATH'] = join(venv_path, 'bin') + ':' + environ['PATH']
    elif join(venv_path, 'bin') not in env['PATH']:
        env['PATH'] = join(venv_path, 'bin') + ':' + env['PATH']
    
    # Make sure virtualenv is set
    if 'VIRTUAL_ENV' not in env:
        env['VIRTUAL_ENV'] = venv_path
    
    # Construct unit name
    unit_name = f"{app}_{kind}.{ordinal}"
    
    log_dir = join(LOG_ROOT, app)
    if not exists(log_dir):
        makedirs(log_dir)

    if kind == 'web':
        pass
    elif kind == 'static':
        return
    elif kind.startswith('cron'):
        # For cron-like jobs, use systemd timer instead of service
        timer_unit = join(SYSTEMD_ROOT, f"{unit_name}.timer")
        service_unit = join(SYSTEMD_ROOT, f"{unit_name}.service")
        
        # Parse the cron pattern from the command
        cron_parts = command.split(' ', 5)
        minute, hour, day, month, weekday, cmd = cron_parts

        # Map numeric weekdays to their names for systemd
        weekday_map = {
            '0': 'Sun', '1': 'Mon', '2': 'Tue', '3': 'Wed', 
            '4': 'Thu', '5': 'Fri', '6': 'Sat'
        }
        
        # Format the calendar specification according to systemd's OnCalendar format
        if weekday != '*':
            # If weekday is specified, use the weekday format
            weekday_str = weekday_map.get(weekday, weekday)
            if day != '*' or month != '*':
                # Both weekday and specific date - systemd requires both conditions
                date_str = f"*-{month if month != '*' else '*'}-{day if day != '*' else '*'}"
                calendar_spec = f"{weekday_str} {date_str} {hour}:{minute.zfill(2)}:00"
            else:
                # Only weekday is specified
                calendar_spec = f"{weekday_str} *-*-* {hour}:{minute.zfill(2)}:00"
        else:
            # No weekday specified, use date format
            date_str = f"*-{month if month != '*' else '*'}-{day if day != '*' else '*'}"
            calendar_spec = f"{date_str} {hour}:{minute.zfill(2)}:00"
        
        # Create timer using the template - add Unit directive to point to our service
        timer_content = SYSTEMD_TIMER_TEMPLATE.format(
            app_name=app,
            process_type=kind,
            calendar_spec=calendar_spec
        ).replace("[Timer]", "[Timer]\nUnit={}.service".format(unit_name))

        # Create a oneshot service that won't restart
        service_content = SYSTEMD_APP_TEMPLATE.format(
            app_name=app,
            process_type=kind,
            instance=ordinal,
            app_path=app_path,
            port=env.get('PORT', '8000'),
            environment_vars='\n'.join(['Environment="{}={}"'.format(k, v) for k, v in env.items()]),
            command=cmd,
            log_path=log_file
        ).replace("Restart=always", "Type=oneshot\nRemainAfterExit=no")

        # Write both files
        with open(timer_unit, 'w', encoding='utf-8') as f:
            f.write(timer_content)
        
        with open(service_unit, 'w', encoding='utf-8') as f:
            f.write(service_content)
        
        # Return the timer unit so it will be properly enabled
        return timer_unit
    # Check if this is a containerized app (Dockerfile present)
    containerized = exists(join(app_path, 'Dockerfile'))
    
    if containerized:
        # For containerized applications, use Podman Quadlet configuration
        container_name = f"{app}-{kind}-{ordinal}"
        image = app
        host_port = env.get('PORT', '8000')
        container_port = env.get('CONTAINER_PORT', host_port)
        app_name = app

        # Create a .container file in the quadlet directory
        quadlet_file = join(QUADLET_ROOT, f"{unit_name}.container")

        # Process additional volume mounts from environment
        extra_volumes = ""
        extra_environment = ""
        command_section = ""

        for key, value in env.items():
            if key.startswith('VOLUME_'):
                src, dest = value.split(':')
                extra_volumes += f"Volume={src}:{dest}\n"
            elif not key.startswith('VOLUME_') and key != 'PORT' and key != 'CONTAINER_PORT':
                extra_environment += f'Environment="{key}={value}"\n'

        # Add command if specified
        if command and command.strip():
            command_section = f"Exec={command}"

        # Format quadlet content using the template
        quadlet_content = QUADLET_CONTAINER_TEMPLATE.format(
            image=image,
            container_name=container_name,
            host_port=host_port,
            container_port=container_port,
            app_path=app_path,
            data_path=data_path,
            extra_volumes=extra_volumes.strip(),
            extra_environment=extra_environment.strip(),
            command_section=command_section,
            log_path=log_file,
            app_name=app_name
        )

        # Write the quadlet file
        with open(quadlet_file, 'w', encoding='utf-8') as f:
            f.write(quadlet_content)

        # Return the created quadlet file
        return quadlet_file
    else:
        # Create regular systemd service file
        # Explicitly include the full PATH environment variable to ensure commands are found
        environment_vars = '\n'.join(['Environment="{}={}"'.format(k, v) for k, v in env.items()])
        # Make sure PATH is explicitly included in environment_vars if not already
        if 'PATH=' not in environment_vars:
            environment_vars += '\nEnvironment="PATH={}"'.format(env['PATH'])
        
        # Convert command to use absolute path for the executable
        abs_command = command
        if ' ' in command:
            # Command has arguments - split to get the executable name
            cmd_parts = command.split(' ', 1)
            cmd_executable = cmd_parts[0]
            cmd_args = cmd_parts[1]
            
            # Create a custom environment with the proper PATH for which
            custom_env = environ.copy()
            custom_env['PATH'] = env['PATH']
            
            # Find the absolute path using the proper PATH (including virtualenv)
            try:
                # Use subprocess to find the absolute path with the correct environment
                abs_executable = command_output(f"which {cmd_executable}").strip()
                if abs_executable:
                    echo(f"-----> Using absolute path for command: {abs_executable} {cmd_args}", fg='green')
                    abs_command = f"{abs_executable} {cmd_args}"
                else:
                    echo(f"Warning: Could not find absolute path for '{cmd_executable}'. Using as is.", fg='yellow')
            except Exception as e:
                echo(f"Warning: Error finding absolute path for '{cmd_executable}': {e}", fg='yellow')
        else:
            # Simple command with no arguments
            custom_env = environ.copy()
            custom_env['PATH'] = env['PATH']
            try:
                abs_executable = command_output(f"which {command}").strip()
                if abs_executable:
                    echo(f"-----> Using absolute path for command: {abs_executable}", fg='green')
                    abs_command = abs_executable
                else:
                    echo(f"Warning: Could not find absolute path for '{command}'. Using as is.", fg='yellow')
            except Exception as e:
                echo(f"Warning: Error finding absolute path for '{command}': {e}", fg='yellow')
        
        unit_content = SYSTEMD_APP_TEMPLATE.format(
            app_name=app,
            process_type=kind,
            instance=ordinal,
            app_path=app_path,
            port=env.get('PORT', '8000'),
            environment_vars=environment_vars,
            command=abs_command,  # Use the command with absolute path
            log_path=log_file
        )
        
        # Write service file directly to systemd user directory
        service_file = join(SYSTEMD_ROOT, f"{unit_name}.service")
        with open(service_file, 'w') as f:
            f.write(unit_content)
        
        # Return the created service file
        return service_file


def do_stop(app):
    """Stop an app by disabling its systemd services"""
    app = sanitize_app_name(app)    
    units = glob(join(SYSTEMD_ROOT, f"{app}_*.service"))
    timers = glob(join(SYSTEMD_ROOT, f"{app}_*.timer"))
    container_files = glob(join(QUADLET_ROOT, f"{app}_*.container"))
    

    if len(units) > 0 or len(container_files) > 0 or len(timers) > 0:
        echo(f"Stopping app '{app}'...", fg='yellow')

        # Handle standard service units
        for unit in units:
            unit_name = basename(unit)
            echo(f"-----> Stopping and disabling: {unit_name}", fg='yellow')
            call(f'systemctl --user stop {unit_name}', shell=True)
            call(f'systemctl --user disable {unit_name}', shell=True)
            
            # Remove the unit file
            try:
                unlink(unit)
                echo(f"-----> Removed unit file: {unit}", fg='green')
            except Exception as e:
                echo(f"Warning: Could not remove unit file {unit}: {e}", fg='yellow')
                
        # Handle timer units for cron jobs
        for timer in timers:
            timer_name = basename(timer)
            service_name = timer_name.replace('.timer', '.service')
            echo(f"-----> Stopping and disabling timer: {timer_name}", fg='yellow')
            call(f'systemctl --user stop {timer_name}', shell=True)
            call(f'systemctl --user disable {timer_name}', shell=True)
            
            # Remove the timer file
            try:
                unlink(timer)
                echo(f"-----> Removed timer file: {timer}", fg='green')
            except Exception as e:
                echo(f"Warning: Could not remove timer file {timer}: {e}", fg='yellow')
            
            # Also remove the associated service if it exists
            service_path = join(SYSTEMD_ROOT, service_name)
            if exists(service_path):
                try:
                    unlink(service_path)
                    echo(f"-----> Removed timer service file: {service_path}", fg='green')
                except Exception as e:
                    echo(f"Warning: Could not remove timer service file {service_path}: {e}", fg='yellow')

        # Handle quadlet container files
        for container_file in container_files:
            container_name = basename(container_file)
            service_name = container_name.replace('.container', '.service')
            
            echo(f"-----> Stopping and disabling container: {service_name}", fg='yellow')
            call(f'systemctl --user stop {service_name}', shell=True)
            call(f'systemctl --user disable {service_name}', shell=True)
            
            # Remove the container file
            try:
                unlink(container_file)
                echo(f"-----> Removed container file: {container_file}", fg='green')
            except Exception as e:
                echo(f"Warning: Could not remove container file {container_file}: {e}", fg='yellow')

        # Reload systemd to recognize all the changes
        call('systemctl --user daemon-reload', shell=True)
        call('systemctl --user reset-failed', shell=True)
    else:
        echo(f"Error: app '{app}' not deployed or already stopped!", fg='red')


def do_restart(app):
    """Restarts a deployed app"""
    app = sanitize_app_name(app)
    # Look only in standard locations
    units = glob(join(SYSTEMD_ROOT, f"{app}_*.service"))
    container_files = glob(join(QUADLET_ROOT, f"{app}_*.container"))

    if len(units) > 0 or len(container_files) > 0:
        echo(f"Restarting app '{app}'...", fg='yellow')

        for unit in units:
            unit_name = basename(unit)
            echo(f"-----> Restarting service: {unit_name}", fg='yellow')
            call(f'systemctl --user restart {unit_name}', shell=True)

        for container_file in container_files:
            service_name = basename(container_file).replace('.container', '.service')
            echo(f"-----> Restarting container: {service_name}", fg='yellow')
            call(f'systemctl --user restart {service_name}', shell=True)
    else:
        echo(f"Error: app '{app}' not deployed!", fg='red')
        do_deploy(app)


def multi_tail(app, filenames, catch_up=20):
    """Tails multiple log files"""

    # Seek helper
    def peek(handle):
        where = handle.tell()
        line = handle.readline()
        if not line:
            handle.seek(where)
            return None
        return line

    inodes = {}
    files = {}
    prefixes = {}

    # Set up current state for each log file
    for f in filenames:
        prefixes[f] = splitext(basename(f))[0]
        files[f] = open(f, "rt", encoding="utf-8", errors="ignore")
        inodes[f] = stat(f).st_ino
        files[f].seek(0, 2)

    longest = max(map(len, prefixes.values()))

    # Grab a little history (if any)
    for f in filenames:
        for line in deque(open(f, "rt", encoding="utf-8", errors="ignore"), catch_up):
            yield "{} | {}".format(prefixes[f].ljust(longest), line)

    while True:
        updated = False
        for f in filenames:
            line = peek(files[f])
            if line:
                updated = True
                yield "{} | {}".format(prefixes[f].ljust(longest), line)

        if not updated:
            sleep(1)
            # Check if logs rotated
            for f in filenames:
                if exists(f):
                    if stat(f).st_ino != inodes[f]:
                        files[f] = open(f)
                        inodes[f] = stat(f).st_ino
                else:
                    filenames.remove(f)

# === CLI commands ===

CONTEXT_SETTINGS = dict(help_option_names=['-h', '--help'])


# --- User commands ---

@command("apps")
def cmd_apps():
    """List apps, e.g.: kata apps"""
    apps = listdir(APP_ROOT)
    if not apps:
        echo("There are no applications deployed.")
        return

    for a in apps:
        units = glob(join(SYSTEMD_ROOT, '{}*.service'.format(a)))
        running = len(units) != 0
        echo(('*' if running else ' ') + a, fg='green')


@command("config")
@argument('app')
def cmd_config(app):
    """Show config, e.g.: kata config <app>"""

    app = exit_if_invalid(app)

    config_file = join(ENV_ROOT, app, 'ENV')
    if exists(config_file):
        echo(open(config_file).read().strip(), fg='white')
    else:
        echo("Warning: app '{}' not deployed, no config found.".format(app), fg='yellow')


@command("config:get")
@argument('app')
@argument('setting')
def cmd_config_get(app, setting):
    """e.g.: kata config:get <app> FOO"""

    app = exit_if_invalid(app)

    config_file = join(ENV_ROOT, app, 'ENV')
    if exists(config_file):
        env = parse_settings(config_file)
        if setting in env:
            echo("{}".format(env[setting]), fg='white')
    else:
        echo("Warning: no active configuration for '{}'".format(app))


@command("config:set")
@argument('app')
@argument('settings', nargs=-1)
def cmd_config_set(app, settings):
    """e.g.: kata config:set <app> FOO=bar BAZ=quux"""

    app = exit_if_invalid(app)

    config_file = join(ENV_ROOT, app, 'ENV')
    env = parse_settings(config_file)
    for s in shsplit(" ".join(settings)):
        try:
            k, v = map(lambda x: x.strip(), s.split("=", 1))
            env[k] = v
            echo("Setting {k:s}={v} for '{app:s}'".format(**locals()), fg='white')
        except Exception:
            echo("Error: malformed setting '{}'".format(s), fg='red')
            return
    write_config(config_file, env)
    do_deploy(app)


@command("config:unset")
@argument('app')
@argument('settings', nargs=-1)
def cmd_config_unset(app, settings):
    """e.g.: kata config:unset <app> FOO"""

    app = exit_if_invalid(app)

    config_file = join(ENV_ROOT, app, 'ENV')
    env = parse_settings(config_file)
    for s in settings:
        if s in env:
            del env[s]
            echo("Unsetting {} for '{}'".format(s, app), fg='white')
    write_config(config_file, env)
    do_deploy(app)


@command("config:live")
@argument('app')
def cmd_config_live(app):
    """e.g.: kata config:live <app>"""

    app = exit_if_invalid(app)

    live_config = join(ENV_ROOT, app, 'LIVE_ENV')
    if exists(live_config):
        echo(open(live_config).read().strip(), fg='white')
    else:
        echo("Warning: app '{}' not deployed, no config found.".format(app), fg='yellow')


@command("caddy")
def cmd_caddy():
    """Display complete Caddy configuration, e.g.: kata caddy"""

    # Get the current Caddy configuration
    config = get_caddy_config()

    if config:
        # Pretty print the JSON configuration
        echo("==== COMPLETE CADDY CONFIGURATION ====", fg='green')
        echo(dumps(config, indent=2), fg='white')
    else:
        echo("No Caddy configuration found. Make sure Caddy is running with the admin API enabled.", fg='yellow')
        echo("The admin API should be available at localhost:2019.", fg='yellow')


@command("caddy:app")
@argument("app")
def cmd_caddy_app(app):
    """Display Caddy configuration for an app, e.g.: kata caddy:app <app>"""

    app = exit_if_invalid(app)

    # Get the current Caddy configuration for the app
    config = get_caddy_config(app)

    if config:
        # Pretty print the JSON configuration, showing only relevant app section
        echo("==== CADDY CONFIGURATION FOR '{}' ====".format(app), fg='green')
        echo(dumps(config, indent=2), fg='white')
        # TODO: add rest of configuration
    else:
        echo("No Caddy configuration found for app '{}'.".format(app), fg='yellow')
        echo("Deploy the app with a caddy.json file to configure Caddy.", fg='yellow')
        echo("Or ensure the app has been deployed successfully.", fg='yellow')


@command("deploy")
@argument('app')
def cmd_deploy(app):
    """e.g.: kata deploy <app>"""

    app = exit_if_invalid(app)
    do_deploy(app)


@command("destroy")
@argument('app')
def cmd_destroy(app):
    """e.g.: kata destroy <app>"""

    app = exit_if_invalid(app)

    # Stop services first
    do_stop(app)

    # Remove Caddy configuration via API
    remove_caddy_config_for_app(app)

    # Remove app directories
    for p in [join(x, app) for x in [APP_ROOT, GIT_ROOT, ENV_ROOT, LOG_ROOT, SYSTEMD_ROOT]]:
        if exists(p):
            echo("--> Removing folder '{}'".format(p), fg='yellow')
            rmtree(p)

    # leave DATA_ROOT, since apps may create hard to reproduce data
    # leave CACHE_ROOT, since apps might use it for important stuff
    for p in [join(x, app) for x in [DATA_ROOT, CACHE_ROOT]]:
        if exists(p):
            echo("==> Preserving folder '{}'".format(p), fg='red')

    # Reload Caddy to remove the app's configuration
    if exists('/etc/systemd/system/caddy.service'):
        echo("-----> Reloading Caddy configuration")
        call('systemctl reload caddy', shell=True)

@command("logs")
@argument('app')
def cmd_logs(app, process='*', follow=False, include_journal=True, startup_only=False):
    """View application logs, e.g: kata logs <app> [<process>]
    
    Use -f or --follow to follow logs in real-time.
    Use -s or --startup to show only startup/error logs.
    """
    app = exit_if_invalid(app)
    
    # Debug what we received (uncomment to troubleshoot)
    # echo(f"DEBUG: Received app argument: '{app}'", fg='yellow')
    
    # Parse arguments from app string if provided via SSH
    if ' ' in app:
        parts = app.split(' ')
        app = sanitize_app_name(parts[0])
        
        # First non-flag argument after app name is the process name
        process_set = False
        for part in parts[1:]:
            if part in ['-f', '--follow']:
                follow = True
            elif part in ['-s', '--startup']:
                startup_only = True
            elif part.startswith('-'):
                continue  # Skip other flags
            elif not process_set:  # Only set process once
                process = part
                process_set = True
                # Add explicit debug output
                echo(f"Setting process filter to: '{process}'", fg='green')
    
    # Define unit pattern for both modes - use exact pattern for process
    if process == '*':
        unit_pattern = f"{app}_*.service"
        log_pattern = f"{process}.*.log"
    else:
        # For exact process matching, be specific about the service pattern
        unit_pattern = f"{app}_{process}.*.service"
        log_pattern = f"{process}.*.log"
    
    # Add debug output to confirm pattern
    echo(f"Looking for systemd units matching: {unit_pattern}", fg='green')
    
    # For follow mode, we need special handling
    if follow:
        echo(f"-----> Following logs for '{app}' worker '{process}'", fg='green')
        
        # Get the log files
        logfiles = glob(join(LOG_ROOT, app, f"{process}.*.log"))
        
        # Start a background process for journalctl if needed
        journal_proc = None
        if include_journal:
            try:
                check_cmd = f"systemctl --user list-units {unit_pattern} --no-legend"
                units_output = command_output(check_cmd)
                
                if units_output.strip():
                    echo(f"-----> Following systemd journal logs", fg='green')
                    journal_cmd = f"journalctl --user -f -u {unit_pattern}"
                    journal_proc = Popen(journal_cmd, shell=True)
            except Exception as e:
                echo(f"Error starting journal follow: {str(e)}", fg='red')
        
        # Now follow the log files if they exist
        if logfiles:
            try:
                echo(f"-----> Following file logs", fg='green')
                try:
                    # Use an infinite loop since multi_tail is a generator
                    for line in multi_tail(app, logfiles):
                        echo(line.strip(), fg='white')
                except KeyboardInterrupt:
                    # Stop the journal process if it's running
                    if journal_proc:
                        journal_proc.terminate()
                    echo("\nStopped following logs", fg='yellow')
                    return
            finally:
                # Make sure to clean up
                if journal_proc:
                    journal_proc.terminate()
        else:
            # If no log files, just wait on the journal process
            if journal_proc:
                try:
                    journal_proc.wait()
                except KeyboardInterrupt:
                    journal_proc.terminate()
                    echo("\nStopped following logs", fg='yellow')
            else:
                echo("No logs found to follow.", fg='yellow')
        return
        
    # Non-follow mode (startup/journal logs)
    if startup_only or include_journal:
        echo(f"-----> Startup logs for '{app}' worker '{process}'", fg='green')
        try:
            # Get a list of all matching units with direct glob instead of parsing systemctl output
            units = glob(join(SYSTEMD_ROOT, unit_pattern))
            
            if units:
                # Show what we found
                echo(f"Found {len(units)} matching systemd units", fg='green')
                # Just use the basenames of the files as the unit names
                unit_names = [basename(unit) for unit in units]
                
                # For each unit, show its status and recent logs
                for unit_name in unit_names:
                    # Get unit status
                    status_cmd = f"systemctl --user status {unit_name} --no-pager"
                    status_output = command_output(status_cmd)
                    
                    echo(f"\n=== LOGS FOR {unit_name} ===", fg='green')
                    
                    if "Failed" in status_output or "error" in status_output.lower():
                        echo(f"SERVICE STATUS:", fg='yellow')
                        # Extract and show the specific error
                        for line in status_output.split('\n'):
                            if "Failed" in line or "error" in line.lower():
                                echo(line.strip(), fg='red')
                    
                    # Get journal logs for this unit
                    log_cmd = f"journalctl --user -u {unit_name} --no-pager -n 25"
                    # Add flags to get more startup info
                    if startup_only:
                        log_cmd += " -p err..emerg"  # Only show error, critical, alert, and emergency messages
                    
                    journal_output = command_output(log_cmd)
                    if journal_output.strip():
                        echo(f"JOURNAL LOGS:", fg='yellow')
                        echo(journal_output.strip(), fg='white')
                    else:
                        echo("No journal logs found for this unit", fg='yellow')
            else:
                echo(f"No systemd units found matching {unit_pattern}", fg='yellow')
        except Exception as e:
            echo(f"Error retrieving systemd logs: {str(e)}", fg='red')
    
    # If not just looking for startup errors, show regular file logs
    if not startup_only:
        echo(f"\n-----> File logs for '{app}' worker '{process}'", fg='green')
        logfiles = glob(join(LOG_ROOT, app, log_pattern))
        if len(logfiles) > 0:
            echo(f"Found {len(logfiles)} matching log files", fg='green')
            for line in multi_tail(app, logfiles):
                echo(line.strip(), fg='white')
        else:
            echo(f"No log files found for app '{app}' with process pattern '{process}'.", fg='yellow')


@command("ps")
@argument('app')
def cmd_ps(app):
    """Show process count, e.g: kata ps <app>"""

    app = exit_if_invalid(app)

    config_file = join(ENV_ROOT, app, 'SCALING')
    if exists(config_file):
        echo(open(config_file).read().strip(), fg='white')
    else:
        echo("Error: no workers found for app '{}'.".format(app), fg='red')


@command("ps:scale")
@argument('app')
@argument('settings', nargs=-1)
def cmd_ps_scale(app, settings):
    """e.g.: kata ps:scale <app> <proc>=<count>"""

    app = exit_if_invalid(app)

    config_file = join(ENV_ROOT, app, 'SCALING')
    worker_count = {k: int(v) for k, v in parse_procfile(config_file).items()}
    deltas = {}
    for s in settings:
        try:
            k, v = map(lambda x: x.strip(), s.split("=", 1))
            c = int(v)  # check for integer value
            if c < 0:
                echo("Error: cannot scale type '{}' below 0".format(k), fg='red')
                return
            if k not in worker_count:
                echo("Error: worker type '{}' not present in '{}'".format(k, app), fg='red')
                return
            deltas[k] = c - worker_count[k]
        except Exception:
            echo("Error: malformed setting '{}'".format(s), fg='red')
            return
    do_deploy(app, deltas)


@command("run")
@argument('app')
@argument('cmd', nargs=-1)
def cmd_run(app, cmd):
    """e.g.: kata run <app> ls -- -al"""

    app = exit_if_invalid(app)

    config_file = join(ENV_ROOT, app, 'LIVE_ENV')
    environ.update(parse_settings(config_file))
    for f in [stdout, stderr]:
        fl = fcntl(f, F_GETFL)
        fcntl(f, F_SETFL, fl | O_NONBLOCK)
    p = Popen(' '.join(cmd), stdin=stdin, stdout=stdout, stderr=stderr, env=environ, cwd=join(APP_ROOT, app), shell=True)
    p.communicate()


@command("restart")
@argument('app')
def cmd_restart(app):
    """Restart an app: kata restart <app>"""

    app = exit_if_invalid(app)

    do_restart(app)


@command("stop")
@argument('app')
def cmd_stop(app):
    """Stop an app, e.g: kata stop <app>"""
    app = exit_if_invalid(app)
    do_stop(app)


@command("setup")
def cmd_setup():
    """Initialize environment"""

    echo("Running in Python {}".format(".".join(map(str, version_info))))

    # Create required paths
    for p in [APP_ROOT, CACHE_ROOT, DATA_ROOT, GIT_ROOT, ENV_ROOT, SYSTEMD_ROOT, QUADLET_ROOT, LOG_ROOT, CADDY_ROOT, PODMAN_ROOT, ACME_WWW]:
        if not exists(p):
            echo("Creating '{}'.".format(p), fg='green')
            makedirs(p)

    # Check for required binaries
    requirements = ['caddy', 'podman', 'podman-compose']
    missing = []
    for req in requirements:
        if not which(req):
            missing.append(req)

    if missing:
        echo("Warning: Missing required binaries: {}".format(', '.join(missing)), fg='yellow')
        echo("You'll need to install these packages before using Kata", fg='yellow')

    # Verify podman-system-generator is available for quadlet integration
    podman_generator = False
    for path in ['/usr/lib/systemd/system-generators/podman-system-generator',
                 '/usr/local/lib/systemd/system-generators/podman-system-generator']:
        if exists(path):
            podman_generator = True
            break

    if not podman_generator:
        echo("Warning: podman-system-generator not found.", fg='yellow')
        echo("Container quadlet functionality may not work correctly.", fg='yellow')
        echo("Make sure you have a recent version of podman with quadlet support.", fg='yellow')

    # mark this script as executable (in case we were invoked via interpreter)
    if not (stat(KATA_SCRIPT).st_mode & S_IXUSR):
        echo("Setting '{}' as executable.".format(KATA_SCRIPT), fg='yellow')
        chmod(KATA_SCRIPT, stat(KATA_SCRIPT).st_mode | S_IXUSR)


@command("setup:ssh")
@argument('public_key_file')
def cmd_setup_ssh(public_key_file):
    """Set up a new SSH key (use - for stdin)"""

    def add_helper(key_file):
        if exists(key_file):
            try:
                fingerprint = str(check_output('ssh-keygen -lf ' + key_file, shell=True)).split(' ', 4)[1]
                key = open(key_file, 'r', encoding='utf-8').read().strip()
                echo(f"Adding key '{fingerprint}'.", fg='white')
                setup_authorized_keys(fingerprint, KATA_SCRIPT, key)
            except Exception:
                echo(f"Error: invalid public key file '{key_file}': {format_exc()}", fg='red')
        elif public_key_file == '-':
            buffer = "".join(stdin.readlines())
            with NamedTemporaryFile(mode="w") as f:
                f.write(buffer)
                f.flush()
                add_helper(f.name)
        else:
            echo("Error: public key file '{}' not found.".format(key_file), fg='red')

    add_helper(public_key_file)


@command("setup:caddy")
def cmd_setup_caddy():
    """Setup caddy.json template file for apps"""
    # Display a sample caddy.json file with environment variable support
    sample = {
        "listen": [":$PORT"],
        "routes": [{
            "match": [{
                "host": ["$DOMAIN_NAME"]
            }],
            "handle": [{
                "handler": "reverse_proxy",
                "upstreams": [{
                    "dial": "$BIND_ADDRESS:$PORT"
                }]
            }]
        }]
    }
    echo("\nSample caddy.json file (place this in your app directory):", fg="green")
    echo(dumps(sample, indent=2), fg="white")
    echo("\nEnvironment variables like $PORT, $DOMAIN_NAME, etc. will be replaced with actual values.", fg="yellow")
    echo("Make sure to set PORT in your ENV file.\n", fg="yellow")

@command("update")
def cmd_update():
    """Update the kata server script"""
    echo("Updating kata...")

    with NamedTemporaryFile(mode="w") as f:
        tempfile = f.name
        cmd = """curl -sL -w %{{http_code}} {} -o {}""".format(KATA_RAW_SOURCE_URL, tempfile)
        response = check_output(cmd.split(' '), stderr=STDOUT)
        http_code = response.decode('utf8').strip()
        if http_code == "200":
            copyfile(tempfile, KATA_SCRIPT)
            echo("Update successful.")
        else:
            echo(f"Error updating kata - please check if {KATA_RAW_SOURCE_URL} is accessible from this machine.", fg='red')
    echo("Done.")

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
@pass_context
def cmd_scp(ctx):
    """Simple wrapper to allow scp to work."""
    call(" ".join(["scp"] + ctx["args"]), cwd=GIT_ROOT, shell=True)

@command("help")
def cmd_help():
    """display help for kata"""
    show_help()

if __name__ == '__main__':
    # Run the CLI with all registered commands
    run_cli()
