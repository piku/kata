#!/usr/bin/env python3

"Kata Micro-PaaS - Kata refactor"

try:
    from sys import version_info
    assert version_info >= (3, 10)
except AssertionError:
    exit("Kata requires Python 3.10 or above")

from importlib import import_module
from collections import defaultdict, deque
from fcntl import fcntl, F_SETFL, F_GETFL
from glob import glob
from json import loads
from multiprocessing import cpu_count
from os import chmod, getgid, getuid, symlink, unlink, remove, stat, listdir, environ, makedirs, O_NONBLOCK
from os.path import abspath, basename, dirname, exists, getmtime, join, realpath, splitext, isdir
from pwd import getpwuid
from grp import getgrgid
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
from urllib.request import urlopen



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
SYSTEMD_ROOT = abspath(join(KATA_ROOT, "systemd"))
PODMAN_ROOT = abspath(join(KATA_ROOT, "podman"))
ACME_ROOT = environ.get('ACME_ROOT', join(environ['HOME'], '.acme.sh'))
ACME_WWW = abspath(join(KATA_ROOT, "acme"))
ACME_ROOT_CA = environ.get('ACME_ROOT_CA', 'letsencrypt.org')
UNIT_PATTERN = "%s@%s.service"

# === Make sure we can access kata user-installed binaries === #

if KATA_BIN not in environ['PATH']:
    environ['PATH'] = KATA_BIN + ":" + environ['PATH']

# Caddy configuration templates
CADDY_MAINFILE_TEMPLATE = """# Main Caddyfile for Kata Micro-PaaS\nimport {import_path}\n"""

CADDY_FILE_TEMPLATE = """
{
  # Global options
  admin off
  persist_config off
  
  # Default TLS settings
  auto_https $CADDY_AUTO_HTTPS
  email $CADDY_EMAIL
  
  # HTTP options
  servers {
    protocol {
      experimental_http3
    }
  }
}

# Site definitions
$CADDY_SITES
"""

CADDY_SITE_TEMPLATE = """
$SITE_NAME {
  $TLS_CONFIG
  
  $CLOUDFLARE_CONFIG
  
  # Static file mappings
  $STATIC_MAPPINGS
  
  # Proxy to application
  $PROXY_CONFIG
  
  # Add headers
  header {
    X-Deployed-By kata
  }
  
  # Enable gzip compression
  encode gzip
  
  $CACHE_CONFIG
}
"""

CADDY_TLS_TEMPLATE = """
  tls {
    issuer acme {
      email $CADDY_EMAIL
      $CADDY_CA_ROOT
    }
  }
"""

CADDY_STATIC_MAPPING = """
  handle_path $static_url {
    root * $static_path
    try_files {path} {path}.html {path}/ $catch_all
    file_server
  }
"""

CADDY_PROXY_TEMPLATE = """
  handle {
    reverse_proxy $APP_ADDRESS {
      header_up X-Forwarded-Proto {scheme}
      header_up X-Forwarded-For {remote}
      header_up X-Real-IP {remote}
      header_up Host {host}
    }
  }
"""

CADDY_CACHE_TEMPLATE = """
  handle_path /$cache_prefixes {
    header Cache-Control "public, max-age=$cache_time_control"
    
    reverse_proxy $APP_ADDRESS {
      header_up X-Forwarded-Proto {scheme}
      header_up X-Forwarded-For {remote}
      header_up X-Real-IP {remote}
      header_up Host {host}
    }
  }
"""

CADDY_CLOUDFLARE_CONFIG = """
  @cloudflare_only {
    remote_ip $CLOUDFLARE_IPS
  }
  
  handle @cloudflare_only {
    # Continue processing for CloudFlare IPs
  }
  
  handle {
    abort
  }
"""

# Systemd unit templates

SYSTEMD_APP_TEMPLATE = """
[Unit]
Description=Kata app: {app_name} - {process_type} {instance}
After=network.target

[Service]
User={user}
Group={group}
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
WantedBy=multi-user.target
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
WantedBy=multi-user.target
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
        return "The smallest PaaS you've ever seen"

def show_help():
    """Show help for all commands"""
    # Get program description
    echo("Kata: The smallest PaaS you've ever seen", fg="green")
    
    echo("\nCommands:", fg="green")
    for cmd_name in sorted(_commands.keys()):
        cmd_info = _commands[cmd_name]
        cmd_help = (cmd_info["doc"] or "").split("\n")[0]
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
    with open(filename, 'w') as h:
        for k, v in bag.items():
            h.write('{k:s}{separator:s}{v}\n'.format(**locals()))


def setup_authorized_keys(ssh_fingerprint, script_path, pubkey):
    """Sets up an authorized_keys file to redirect SSH commands"""
    authorized_keys = join(environ['HOME'], '.ssh', 'authorized_keys')
    if not exists(dirname(authorized_keys)):
        makedirs(dirname(authorized_keys))
    # Restrict features and force all SSH commands to go through our script
    with open(authorized_keys, 'a') as h:
        h.write("""command="FINGERPRINT={ssh_fingerprint:s} NAME=default {script_path:s} $SSH_ORIGINAL_COMMAND",no-agent-forwarding,no-user-rc,no-X11-forwarding,no-port-forwarding {pubkey:s}\n""".format(**locals()))
    chmod(dirname(authorized_keys), S_IRUSR | S_IWUSR | S_IXUSR)
    chmod(authorized_keys, S_IRUSR | S_IWUSR)


def parse_procfile(filename):
    """Parses a Procfile and returns the worker types. Only one worker of each type is allowed."""
    workers = {}
    if not exists(filename):
        return None

    with open(filename, 'r') as procfile:
        for line_number, line in enumerate(procfile):
            line = line.strip()
            if line.startswith("#") or not line:
                continue
            try:
                kind, command = map(lambda x: x.strip(), line.split(":", 1))
                # Check for cron patterns
                if kind.startswith("cron"):
                    limits = [59, 24, 31, 12, 7]
                    res = match(r"^((?:(?:\*\/)?\d+)|\*) ((?:(?:\*\/)?\d+)|\*) ((?:(?:\*\/)?\d+)|\*) ((?:(?:\*\/)?\d+)|\*) ((?:(?:\*\/)?\d+)|\*) (.*)$", command)
                    if res:
                        matches = res.groups()
                        for i in range(len(limits)):
                            if int(matches[i].replace("*/", "").replace("*", "1")) > limits[i]:
                                raise ValueError
                workers[kind] = command
            except Exception:
                echo("Warning: misformatted Procfile entry '{}' at line {}".format(line, line_number), fg='yellow')
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
        return str(check_output(cmd, stderr=STDOUT, env=env, shell=True))
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
            except Exception:
                echo("Error: malformed setting '{}', ignoring file.".format(line), fg='red')
                return {}
    return env


def check_requirements(binaries):
    """Checks if all the binaries exist and are executable"""
    echo("-----> Checking requirements: {}".format(binaries), fg='green')
    requirements = list(map(which, binaries))
    echo(str(requirements))

    if None in requirements:
        return False
    return True


def found_app(kind):
    """Helper function to output app detected"""
    echo("-----> {} app detected.".format(kind), fg='green')
    return True


def do_deploy(app, deltas={}, newrev=None):
    """Deploy an app by resetting the work directory"""

    app_path = join(APP_ROOT, app)
    procfile = join(app_path, 'Procfile')
    log_path = join(LOG_ROOT, app)

    env = {'GIT_WORK_DIR': app_path}
    if exists(app_path):
        echo("-----> Deploying app '{}'".format(app), fg='green')
        call('git fetch --quiet', cwd=app_path, env=env, shell=True)
        if newrev:
            call('git reset --hard {}'.format(newrev), cwd=app_path, env=env, shell=True)
        call('git submodule init', cwd=app_path, env=env, shell=True)
        call('git submodule update', cwd=app_path, env=env, shell=True)
        if not exists(log_path):
            makedirs(log_path)
        workers = parse_procfile(procfile)
        if workers and len(workers) > 0:
            settings = {}
            if "preflight" in workers:
                echo("-----> Running preflight.", fg='green')
                retval = call(workers["preflight"], cwd=app_path, env=settings, shell=True)
                if retval:
                    echo("-----> Exiting due to preflight command error value: {}".format(retval))
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
            elif exists(join(app_path, 'Dockerfile')) and found_app("Containerized") and check_requirements(['podman']):
                settings.update(deploy_containerized(app, deltas))
                deployed = True
            elif exists(join(app_path, 'docker-compose.yaml')) and found_app("Docker Compose") and check_requirements(['podman-compose']):
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
                echo("-----> Could not detect runtime!", fg='red')
                echo("-----> Only Python and containerized apps are currently supported.", fg='yellow')
            
            if "release" in workers:
                echo("-----> Releasing", fg='green')
                retval = call(workers["release"], cwd=app_path, env=settings, shell=True)
                if retval:
                    echo("-----> Exiting due to release command error value: {}".format(retval))
                    exit(retval)
                workers.pop("release", None)
        else:
            echo("Error: Invalid Procfile for app '{}'.".format(app), fg='red')
    else:
        echo("Error: app '{}' not found.".format(app), fg='red')


def deploy_python(app, deltas={}):
    """Deploy a Python application"""

    virtualenv_path = join(ENV_ROOT, app)
    requirements = join(APP_ROOT, app, 'requirements.txt')
    env_file = join(APP_ROOT, app, 'ENV')
    # Set unbuffered output and readable UTF-8 mapping
    env = {
        'PYTHONUNBUFFERED': '1',
        'PYTHONIOENCODING': 'UTF_8:replace'
    }
    if exists(env_file):
        env.update(parse_settings(env_file, env))

    # TODO: improve version parsing
    # pylint: disable=unused-variable
    version = int(env.get("PYTHON_VERSION", "3"))

    first_time = False
    if not exists(join(virtualenv_path, "bin", "activate")):
        echo("-----> Creating virtualenv for '{}'".format(app), fg='green')
        try:
            makedirs(virtualenv_path)
        except FileExistsError:
            echo("-----> Env dir already exists: '{}'".format(app), fg='yellow')
        call('virtualenv --python=python{version:d} {app:s}'.format(**locals()), cwd=ENV_ROOT, shell=True)
        first_time = True

    activation_script = join(virtualenv_path, 'bin', 'activate_this.py')
    exec(open(activation_script).red(), dict(__file__=activation_script))

    if first_time or getmtime(requirements) > getmtime(virtualenv_path):
        echo("-----> Running pip for '{}'".format(app), fg='green')
        call('pip install -r {}'.format(requirements), cwd=virtualenv_path, shell=True)
    return spawn_app(app, deltas)


def deploy_python_with_uv(app, deltas={}):
    """Deploy a Python application using Astral uv"""

    echo("=====> Starting uv deployment for '{}'".format(app), fg='green')
    env_file = join(APP_ROOT, app, 'ENV')
    virtualenv_path = join(ENV_ROOT, app)
    # Set unbuffered output and readable UTF-8 mapping
    env = {
        **environ,
        'PYTHONUNBUFFERED': '1',
        'PYTHONIOENCODING': 'UTF_8:replace',
        'UV_PROJECT_ENVIRONMENT': virtualenv_path
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
    
    echo("-----> Building container for '{}'".format(app), fg='green')
    call('podman build -t {app:s} .'.format(**locals()), cwd=app_path, shell=True)
    
    # Ensure quadlet directory exists
    podman_dir = join(PODMAN_ROOT, app)
    if not exists(podman_dir):
        makedirs(podman_dir)
    
    echo("-----> Setting up Podman Quadlet configuration for '{}'".format(app), fg='green')
    
    return spawn_app(app, deltas)


def deploy_compose(app, deltas={}):
    """Deploy an application using podman-compose"""
    
    app_path = join(APP_ROOT, app)
    env_file = join(APP_ROOT, app, 'ENV')
    env = {}
    
    if exists(env_file):
        env.update(parse_settings(env_file, env))
    
    echo("-----> Setting up podman-compose for '{}'".format(app), fg='green')
    # Just prepare the environment, actual compose launch will be done through systemd
    
    return spawn_app(app, deltas)


def spawn_app(app, deltas={}):
    """Create all workers for an app using systemd units and Caddy"""

    app_path = join(APP_ROOT, app)
    procfile = join(app_path, 'Procfile')
    workers = parse_procfile(procfile)
    workers.pop("preflight", None)
    workers.pop("release", None)
    ordinals = defaultdict(lambda: 1)
    worker_count = {k: 1 for k in workers.keys()}

    # the Python virtualenv
    virtualenv_path = join(ENV_ROOT, app)
    # Settings shipped with the app
    env_file = join(APP_ROOT, app, 'ENV')
    # Custom overrides
    settings = join(ENV_ROOT, app, 'ENV')
    # Live settings
    live = join(ENV_ROOT, app, 'LIVE_ENV')
    # Scaling
    scaling = join(ENV_ROOT, app, 'SCALING')

    # Bootstrap environment
    env = {
        'APP': app,
        'LOG_ROOT': LOG_ROOT,
        'DATA_ROOT': join(DATA_ROOT, app),
        'HOME': environ['HOME'],
        'USER': environ['USER'],
        'PATH': ':'.join([join(virtualenv_path, 'bin'), environ['PATH']]),
        'PWD': dirname(env_file),
        'VIRTUAL_ENV': virtualenv_path,
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

    if 'web' in workers or 'wsgi' in workers or 'static' in workers:
        echo("-----> Generating Caddy configuration", fg='green')
        # Pick a port if none defined
        if 'PORT' not in env:
            env['PORT'] = str(get_free_port())
            echo("-----> picking free port {PORT}".format(**env))

        # Safe defaults for addressing
        for k, v in safe_defaults.items():
            if k not in env:
                echo("-----> {k:s} will be set to {v}".format(**locals()))
                env[k] = v

        # Generate Caddy configuration if domain name is set
        if 'CADDY_DOMAIN' in env:
            # Ensure CADDY_DOMAIN is treated as a list
            env['CADDY_DOMAIN'] = env['CADDY_DOMAIN'].split(',')
            env['CADDY_DOMAIN'] = ' '.join(env['CADDY_DOMAIN'])

            caddy_file = join(CADDY_ROOT, "{}.caddy".format(app))
            
            # Set up environment variables for Caddy
            env.update({
                'CADDY_AUTO_HTTPS': 'on',
                'CADDY_EMAIL': env.get('CADDY_EMAIL', 'admin@example.com'),
                'CADDY_CA_ROOT': '' if ACME_ROOT_CA == 'letsencrypt.org' else 'ca ' + ACME_ROOT_CA,
                'SITE_NAME': env['CADDY_DOMAIN'],
            })

            # Set up TLS if enabled
            tls_config = ''
            if not get_boolean(env.get('CADDY_DISABLE_TLS', 'false')):
                tls_config = expandvars(CADDY_TLS_TEMPLATE, env)

            # Set up CloudFlare config if enabled
            cloudflare_config = ''
            if get_boolean(env.get('CADDY_CLOUDFLARE_ACL', 'false')):
                try:
                    cf = loads(urlopen('https://api.cloudflare.com/client/v4/ips').read().decode("utf-8"))
                    if cf['success'] is True:
                        cloudflare_ips = []
                        for i in cf['result']['ipv4_cidrs']:
                            cloudflare_ips.append(i)
                        for i in cf['result']['ipv6_cidrs']:
                            cloudflare_ips.append(i)
                        # Add the client's IP
                        if 'SSH_CLIENT' in environ:
                            remote_ip = environ['SSH_CLIENT'].split()[0]
                            echo("-----> Caddy ACL will include your IP ({})".format(remote_ip))
                            cloudflare_ips.append(remote_ip)
                        env['CLOUDFLARE_IPS'] = ' '.join(cloudflare_ips)
                        cloudflare_config = expandvars(CADDY_CLOUDFLARE_CONFIG, env)
                except Exception:
                    echo("-----> Could not retrieve CloudFlare IP ranges: {}".format(format_exc()), fg="red")

            # Set up static file mappings
            static_mappings = ''
            static_paths = env.get('STATIC_PATHS', '')
            if 'static' in workers:
                stripped = workers['static'].strip("/").rstrip("/")
                static_paths = ("/" if stripped[0:1] == ":" else "/:") + (stripped if stripped else ".") + "/" + ("," if static_paths else "") + static_paths
            
            if len(static_paths):
                try:
                    catch_all = env.get('CATCH_ALL', '')
                    items = static_paths.split(',')
                    for item in items:
                        static_url, static_path = item.split(':')
                        if static_path[0] != '/':
                            static_path = join(app_path, static_path).rstrip("/") + "/"
                        echo("-----> Caddy will map {} to {}.".format(static_url, static_path))
                        static_mappings += expandvars(CADDY_STATIC_MAPPING, locals())
                except Exception as e:
                    echo("Error {} in static path spec: should be /prefix1:path1[,/prefix2:path2], ignoring.".format(e))
                    static_mappings = ''
            
            # Set up cache configuration if cache prefixes are defined
            cache_config = ''
            cache_prefixes = env.get('CACHE_PREFIXES', '')
            if len(cache_prefixes):
                try:
                    try:
                        cache_time_control = int(env.get('CACHE_CONTROL', '3600'))
                    except Exception:
                        echo("=====> Invalid time for cache control, defaulting to 3600s")
                        cache_time_control = 3600
                    
                    prefixes = []
                    items = cache_prefixes.split(',')
                    for item in items:
                        if item[0] == '/':
                            prefixes.append(item[1:])
                        else:
                            prefixes.append(item)
                    cache_prefixes = "|".join(prefixes)
                    
                    echo("-----> Caddy will cache /({}) paths".format(cache_prefixes))
                    echo("-----> Caddy will send caching headers asking for {} seconds of public caching.".format(cache_time_control))
                    
                    cache_config = expandvars(CADDY_CACHE_TEMPLATE, locals())
                except Exception as e:
                    echo("Error {} in cache path spec: should be /prefix1:[,/prefix2], ignoring.".format(e))
                    cache_config = ''
            
            # Set up the proxy configuration to the application
            app_address = ''
            if 'web' in workers or 'wsgi' in workers:
                app_address = "{BIND_ADDRESS:s}:{PORT:s}".format(**env)
                echo("-----> Caddy will proxy to app '{}' at {}".format(app, app_address))
                env['APP_ADDRESS'] = app_address
                proxy_config = expandvars(CADDY_PROXY_TEMPLATE, env)
            else:
                proxy_config = ''
            
            # Assemble the site configuration
            site_config = expandvars(CADDY_SITE_TEMPLATE, {
                'SITE_NAME': env['SITE_NAME'],
                'TLS_CONFIG': tls_config,
                'CLOUDFLARE_CONFIG': cloudflare_config,
                'STATIC_MAPPINGS': static_mappings,
                'PROXY_CONFIG': proxy_config,
                'CACHE_CONFIG': cache_config
            })
            
            # Create the full Caddy configuration
            caddy_config = expandvars(CADDY_FILE_TEMPLATE, {
                'CADDY_AUTO_HTTPS': env.get('CADDY_AUTO_HTTPS', 'on'),
                'CADDY_EMAIL': env['CADDY_EMAIL'],
                'CADDY_SITES': site_config
            })
            
            # Write the Caddy configuration file
            with open(caddy_file, 'w') as f:
                f.write(caddy_config)
            
            # Reload Caddy to apply the new configuration
            if exists('/etc/systemd/system/caddy.service'):
                echo("-----> Reloading Caddy configuration")
                call('systemctl reload caddy', shell=True)
            else:
                echo("Warning: Caddy systemd service not found. Please reload Caddy manually.", fg='yellow')

    # Configured worker count
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
    # Ensure the directory for LIVE_ENV exists before writing
    live_dir = dirname(live)
    if not exists(live_dir):
        makedirs(live_dir)
    write_config(live, env)
    write_config(scaling, worker_count, ':')

    # Create systemd directories if they don't exist
    if not exists(SYSTEMD_ROOT):
        makedirs(SYSTEMD_ROOT)
    
    user_systemd_dir = join(environ['HOME'], '.config', 'systemd', 'user')
    if not exists(user_systemd_dir):
        makedirs(user_systemd_dir)

    # Create new workers
    for k, v in to_create.items():
        if k == "static":
            continue  # Skip static workers for systemd unit/symlink creation
        for w in v:
            unit_name = "{app:s}_{k:s}.{w:d}".format(**locals())
            unit_file = join(SYSTEMD_ROOT, unit_name + '.service')
            unit_link = join(user_systemd_dir, unit_name + '.service')
            
            if not exists(unit_file):
                echo("-----> Spawning '{app:s}:{k:s}.{w:d}'".format(**locals()), fg='green')
                spawn_worker(app, k, workers[k], env, w, unit_file)
                
                # If it's a container file, we need special handling for quadlet
                is_container = unit_file.endswith('.container')
                
                # Create quadlet directory if needed
                quadlet_dir = join(user_systemd_dir, 'containers', 'systemd')
                if is_container and not exists(quadlet_dir):
                    makedirs(quadlet_dir, exist_ok=True)
                
                # Use appropriate destination for symlink
                symlink_dest = unit_file
                symlink_target = unit_link if not is_container else join(quadlet_dir, basename(unit_file))
                
                # Create symlink to user systemd directory if it doesn't exist
                if exists(symlink_target):
                    try:
                        unlink(symlink_target)
                        echo(f"-----> Removed existing symlink: {symlink_target}", fg='yellow')
                    except Exception as e:
                        echo(f"Warning: Could not remove existing symlink {symlink_target}: {e}", fg='yellow')
                symlink(symlink_dest, symlink_target)
                
                # For container files, we need to run daemon-reload to generate the service
                if is_container:
                    call('systemctl --user daemon-reload', shell=True)
                    # The actual service name is generated by podman
                    unit_name = basename(unit_file).replace('.container', '.service')
                
                # Enable and start the service
                call('systemctl --user enable {}'.format(unit_name), shell=True)
                call('systemctl --user start {}'.format(unit_name), shell=True)

    # Remove unnecessary workers
    for k, v in to_destroy.items():
        for w in v:
            unit_name = "{app:s}_{k:s}.{w:d}".format(**locals())
            unit_file = join(SYSTEMD_ROOT, unit_name + '.service')
            unit_link = join(user_systemd_dir, unit_name + '.service')
            
            if exists(unit_file):
                echo("-----> Terminating '{app:s}:{k:s}.{w:d}'".format(**locals()), fg='yellow')
                
                # Stop and disable the service
                call('systemctl --user stop {}'.format(unit_name), shell=True)
                call('systemctl --user disable {}'.format(unit_name), shell=True)
                
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
                if exists(unit_link):
                    unlink(unit_link)
                if exists(unit_file):
                    unlink(unit_file)

    return env


def spawn_worker(app, kind, command, env, ordinal=1, unit_file=None):
    """Set up and deploy a single worker of a given kind using systemd"""

    # pylint: disable=unused-variable
    env['PROC_TYPE'] = kind
    env_path = join(ENV_ROOT, app)
    app_path = join(APP_ROOT, app)
    log_file = join(LOG_ROOT, app, "{kind}.{ordinal}.log".format(**locals()))
    data_path = join(DATA_ROOT, app)
    
    # Ensure log directory exists
    log_dir = join(LOG_ROOT, app)
    if not exists(log_dir):
        makedirs(log_dir)

    # Set up specific worker types
    if kind == 'wsgi':
        # For WSGI applications, use gunicorn
        module = command
        # Add gunicorn to command with appropriate settings
        command = join(env_path, 'bin', 'gunicorn')
        command += ' --bind={BIND_ADDRESS:s}:{PORT:s}'.format(**env)
        command += ' --workers={}'.format(env.get('GUNICORN_WORKERS', cpu_count() * 2 + 1))
        command += ' --threads={}'.format(env.get('GUNICORN_THREADS', '4'))
        command += ' ' + module
    elif kind == 'web':
        # Web workers run as-is
        pass
    elif kind == 'static':
        # Static workers don't need a systemd unit, skip creation
        return
    elif kind.startswith('cron'):
        # For cron-like jobs, use systemd timer instead of service
        timer_unit = unit_file.replace('.service', '.timer')
        # Parse the cron pattern from the command
        cron_parts = command.split(' ', 5)
        minute, hour, day, month, weekday, cmd = cron_parts
        
        # Convert cron pattern to systemd timer format
        # This is a simplified conversion and might need enhancement for complex patterns
        # Build the systemd calendar specification string using f-strings
        weekday_str = ('Sat' if weekday == '6' else 
                       'Sun' if weekday == '0' else 
                       'Mon' if weekday == '1' else 
                       'Tue' if weekday == '2' else 
                       'Wed' if weekday == '3' else 
                       'Thu' if weekday == '4' else 
                       'Fri' if weekday == '5' else '*')
        
        month_str = month if month != '*' else '*'
        day_str = day if day != '*' else '*'
        hour_str = hour if hour != '*' else '*'
        minute_str = minute if minute != '*' else '*'
        
        # Format the calendar specification using f-strings
        calendar_spec = f"{weekday_str} {month_str} {day_str} {hour_str} {minute_str}"
        
        # Format the timer content using the template
        timer_content = SYSTEMD_TIMER_TEMPLATE.format(
            app_name=app,
            process_type=kind,
            calendar_spec=calendar_spec
        )
        
        with open(timer_unit, 'w') as f:
            f.write(timer_content)
        
        # Update command to be the actual command part
        command = cmd
    
    # Check if this is a containerized app (Dockerfile present)
    containerized = exists(join(app_path, 'Dockerfile'))
    
    # Create systemd unit file
    unit_content = ''
    if containerized:
        # For containerized applications, use Podman Quadlet configuration
        container_name = f"{app}-{kind}-{ordinal}"
        image = app
        host_port = env.get('PORT', '8000')
        container_port = env.get('CONTAINER_PORT', host_port)
        app_name = app
        
        # Create a .container file for quadlet instead of a direct systemd unit
        quadlet_file = unit_file.replace('.service', '.container')
        
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
        with open(quadlet_file, 'w') as f:
            f.write(quadlet_content)
        
        # Change the unit_file to point to the quadlet file
        # The actual .service will be generated by the podman-systemd generator
        unit_file = quadlet_file
    else:
        # For regular applications
        # Format the environment variables for systemd
        environment_vars = '\n'.join(['Environment="{}={}"'.format(k, v) for k, v in env.items()])
        
        unit_content = SYSTEMD_APP_TEMPLATE.format(
            app_name=app,
            process_type=kind,
            instance=ordinal,
            user=getpwuid(getuid()).pw_name,
            group=getgrgid(getgid()).gr_name,
            app_path=app_path,
            port=env.get('PORT', '8000'),
            environment_vars=environment_vars,
            command=command,
            log_path=log_file
        )
    
    # Write the unit file
    with open(unit_file, 'w') as f:
        f.write(unit_content)


def do_stop(app):
    """Stop an app by disabling its systemd services"""
    app = sanitize_app_name(app)
    user_systemd_dir = join(environ['HOME'], '.config', 'systemd', 'user')
    quadlet_dir = join(user_systemd_dir, 'containers', 'systemd')
    
    # Find all systemd units for this app (standard units)
    units = glob(join(SYSTEMD_ROOT, '{}*.service'.format(app)))
    
    # Also find all quadlet .container files
    container_files = glob(join(SYSTEMD_ROOT, '{}*.container'.format(app)))
    
    if len(units) > 0 or len(container_files) > 0:
        echo("Stopping app '{}'...".format(app), fg='yellow')
        
        # Handle standard service units
        for unit in units:
            unit_name = basename(unit)
            # Stop and disable the service
            call('systemctl --user stop {}'.format(unit_name), shell=True)
            call('systemctl --user disable {}'.format(unit_name), shell=True)
            
            # Remove symlink from user systemd directory
            unit_link = join(user_systemd_dir, unit_name)
            if exists(unit_link):
                unlink(unit_link)
        
        # Handle quadlet container files
        for container_file in container_files:
            container_name = basename(container_file)
            service_name = container_name.replace('.container', '.service')
            
            # Stop and disable the service
            call('systemctl --user stop {}'.format(service_name), shell=True)
            call('systemctl --user disable {}'.format(service_name), shell=True)
            
            # Remove symlink from quadlet directory
            quadlet_link = join(quadlet_dir, container_name)
            if exists(quadlet_link):
                unlink(quadlet_link)
            
            # Run daemon-reload to update systemd
            call('systemctl --user daemon-reload', shell=True)
    else:
        echo("Error: app '{}' not deployed or already stopped!".format(app), fg='red')


def do_restart(app):
    """Restarts a deployed app"""
    app = sanitize_app_name(app)
    user_systemd_dir = join(environ['HOME'], '.config', 'systemd', 'user')
    
    # Find all systemd units for this app
    units = glob(join(SYSTEMD_ROOT, '{}*.service'.format(app)))
    
    # Also find all quadlet .container files
    container_files = glob(join(SYSTEMD_ROOT, '{}*.container'.format(app)))
    
    if len(units) > 0 or len(container_files) > 0:
        echo("Restarting app '{}'...".format(app), fg='yellow')
        
        # Handle standard service units
        for unit in units:
            unit_name = basename(unit)
            # Restart the service
            call('systemctl --user restart {}'.format(unit_name), shell=True)
        
        # Handle quadlet container files
        for container_file in container_files:
            service_name = basename(container_file).replace('.container', '.service')
            # Restart the service
            call('systemctl --user restart {}'.format(service_name), shell=True)
    else:
        echo("Error: app '{}' not deployed!".format(app), fg='red')
        # Try to deploy it
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
        # Check for updates on every file
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

    # Remove app directories
    for p in [join(x, app) for x in [APP_ROOT, GIT_ROOT, ENV_ROOT, LOG_ROOT, SYSTEMD_ROOT]]:
        if exists(p):
            echo("--> Removing folder '{}'".format(p), fg='yellow')
            rmtree(p)

    # Remove Caddy config
    caddy_file = join(CADDY_ROOT, "{}.caddy".format(app))
    if exists(caddy_file):
        echo("--> Removing file '{}'".format(caddy_file), fg='yellow')
        remove(caddy_file)

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
@argument('process', nargs=1)
def cmd_logs(app, process='*'):
    """Tail running logs, e.g: kata logs <app> [<process>]"""

    app = exit_if_invalid(app)

    logfiles = glob(join(LOG_ROOT, app, process + '.*.log'))
    if len(logfiles) > 0:
        for line in multi_tail(app, logfiles):
            echo(line.strip(), fg='white')
    else:
        echo("No logs found for app '{}'.".format(app), fg='yellow')


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
    for p in [APP_ROOT, CACHE_ROOT, DATA_ROOT, GIT_ROOT, ENV_ROOT, SYSTEMD_ROOT, LOG_ROOT, CADDY_ROOT, PODMAN_ROOT, ACME_WWW]:
        if not exists(p):
            echo("Creating '{}'.".format(p), fg='green')
            makedirs(p)

    # Create user systemd directory
    user_systemd_dir = join(environ['HOME'], '.config', 'systemd', 'user')
    if not exists(user_systemd_dir):
        echo("Creating '{}'.".format(user_systemd_dir), fg='green')
        makedirs(user_systemd_dir)
    
    # Create systemd user quadlet directory
    quadlet_dir = join(user_systemd_dir, 'containers', 'systemd')
    if not exists(quadlet_dir):
        echo("Creating quadlet directory '{}'.".format(quadlet_dir), fg='green')
        makedirs(quadlet_dir)

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


@command("setup:caddy")
def cmd_setup_caddy():
    """Set up the main Caddyfile to import all per-app .caddy files."""

    caddy_import_path = join(environ['HOME'], ".kata", "caddy", "*.caddy")
    main_caddyfile = CADDY_MAINFILE_TEMPLATE.format(import_path=caddy_import_path)

    # Write reference copy to ~/.kata/Caddyfile
    kata_caddyfile_path = join(environ["HOME"], ".kata", "Caddyfile")
    makedirs(dirname(kata_caddyfile_path), exist_ok=True)
    with open(kata_caddyfile_path, "w") as f:
        f.write(main_caddyfile)

    # Write to /etc/caddy/Caddyfile using sudo
    try:
        call(f"cp /etc/caddy/Caddyfile {KATA_ROOT}", shell=True)
        with NamedTemporaryFile(mode="w") as f:
            tempfile = f.name
            with open(tempfile, "w") as f:
                f.write(main_caddyfile)
            call(f"sudo cp {tempfile} /etc/caddy/Caddyfile",  cwd=KATA_ROOT, shell=True)
        echo(f"Main Caddyfile installed to /etc/caddy/Caddyfile. Original file in {KATA_ROOT}.", fg='green')
        echo("Please reload Caddy: sudo systemctl reload caddy")
    except Exception as e:
        echo(f"Error installing Caddyfile: {e}", fg='red')
        

@command("update")
def cmd_update():
    """Update the kata cli"""
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
            echo("Error updating kata - please check if {} is accessible from this machine.".format(KATA_RAW_SOURCE_URL))
    echo("Done.")


# --- Internal commands ---

@command("git-hook")
@argument('app')
def cmd_git_hook(app):
    """INTERNAL: Post-receive git hook"""

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
    """INTERNAL: Handle git pushes for an app"""

    app = sanitize_app_name(app)
    hook_path = join(GIT_ROOT, app, 'hooks', 'post-receive')
    env = globals()
    env.update(locals())

    if not exists(hook_path):
        makedirs(dirname(hook_path))
        # Initialize the repository with a hook to this script
        call("git init --quiet --bare " + app, cwd=GIT_ROOT, shell=True)
        with open(hook_path, 'w') as h:
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
    """INTERNAL: Handle git upload pack for an app"""
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
