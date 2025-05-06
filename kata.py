#!/usr/bin/env python3

"Kata Micro-PaaS - Piku refactor"

try:
    from sys import version_info
    assert version_info >= (3, 8)
except AssertionError:
    exit("Piku requires Python 3.8 or above")

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

from click import argument, group, secho as echo, pass_context, CommandCollection

# === Make sure we can access all system and user binaries ===

if 'sbin' not in environ['PATH']:
    environ['PATH'] = "/usr/local/sbin:/usr/sbin:/sbin:" + environ['PATH']
if '.local' not in environ['PATH']:
    environ['PATH'] = environ['HOME'] + "/.local/bin:" + environ['PATH']

# === Globals - all tweakable settings are here ===

PIKU_RAW_SOURCE_URL = "https://raw.githubusercontent.com/piku/piku/master/refactor.py"
PIKU_ROOT = environ.get('PIKU_ROOT', join(environ['HOME'], '.piku'))
PIKU_BIN = join(environ['HOME'], 'bin')
PIKU_SCRIPT = realpath(__file__)
PIKU_PLUGIN_ROOT = abspath(join(PIKU_ROOT, "plugins"))
APP_ROOT = abspath(join(PIKU_ROOT, "apps"))
DATA_ROOT = abspath(join(PIKU_ROOT, "data"))
ENV_ROOT = abspath(join(PIKU_ROOT, "envs"))
GIT_ROOT = abspath(join(PIKU_ROOT, "repos"))
LOG_ROOT = abspath(join(PIKU_ROOT, "logs"))
CADDY_ROOT = abspath(join(PIKU_ROOT, "caddy"))
CACHE_ROOT = abspath(join(PIKU_ROOT, "cache"))
SYSTEMD_ROOT = abspath(join(PIKU_ROOT, "systemd"))
PODMAN_ROOT = abspath(join(PIKU_ROOT, "podman"))
ACME_ROOT = environ.get('ACME_ROOT', join(environ['HOME'], '.acme.sh'))
ACME_WWW = abspath(join(PIKU_ROOT, "acme"))
ACME_ROOT_CA = environ.get('ACME_ROOT_CA', 'letsencrypt.org')
UNIT_PATTERN = "%s@%s.service"

# === Make sure we can access piku user-installed binaries === #

if PIKU_BIN not in environ['PATH']:
    environ['PATH'] = PIKU_BIN + ":" + environ['PATH']

# Caddy configuration templates

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
    X-Deployed-By Piku
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
Description=Piku app: {app_name} - {process_type} {instance}
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

SYSTEMD_PODMAN_TEMPLATE = """
[Unit]
Description=Piku app: {app_name} (containerized)
After=network.target

[Service]
User={user}
Group={group}
WorkingDirectory={app_path}
Environment="PORT={port}"
{environment_vars}
ExecStartPre=/usr/bin/podman pull {image}
ExecStart=/usr/bin/podman run --rm --name={container_name} \\
    -p {host_port}:{container_port} \\
    -v {app_path}:/app \\
    -v {data_path}:/data \\
    {volume_mounts} \\
    {env_args} \\
    {image} {command}
ExecStop=/usr/bin/podman stop -t 10 {container_name}
Restart=always
RestartSec=10
StandardOutput=append:{log_path}
StandardError=append:{log_path}
SyslogIdentifier={app_name}-container

[Install]
WantedBy=multi-user.target
"""

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
            if exists(join(app_path, 'requirements.txt')) and found_app("Python"):
                settings.update(deploy_python(app, deltas))
            elif exists(join(app_path, 'pyproject.toml')) and which('poetry') and found_app("Python"):
                settings.update(deploy_python_with_poetry(app, deltas))
            elif exists(join(app_path, 'pyproject.toml')) and which('uv') and found_app("Python (uv)"):
                settings.update(deploy_python_with_uv(app, deltas))
            elif exists(join(app_path, 'Dockerfile')) and found_app("Containerized") and check_requirements(['podman']):
                settings.update(deploy_containerized(app, deltas))
            elif exists(join(app_path, 'docker-compose.yaml')) and found_app("Docker Compose") and check_requirements(['podman-compose']):
                settings.update(deploy_compose(app, deltas))
            else:
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
    exec(open(activation_script).read(), dict(__file__=activation_script))

    if first_time or getmtime(requirements) > getmtime(virtualenv_path):
        echo("-----> Running pip for '{}'".format(app), fg='green')
        call('pip install -r {}'.format(requirements), cwd=virtualenv_path, shell=True)
    return spawn_app(app, deltas)


def deploy_python_with_poetry(app, deltas={}):
    """Deploy a Python application using Poetry"""

    echo("=====> Starting poetry deployment for '{}'".format(app), fg='green')
    virtualenv_path = join(ENV_ROOT, app)
    requirements = join(APP_ROOT, app, 'pyproject.toml')
    env_file = join(APP_ROOT, app, 'ENV')
    symlink_path = join(APP_ROOT, app, '.venv')
    if not exists(symlink_path):
        echo("-----> Creating .venv symlink '{}'".format(app), fg='green')
        symlink(virtualenv_path, symlink_path, target_is_directory=True)
    # Set unbuffered output and readable UTF-8 mapping
    env = {
        **environ,
        'POETRY_VIRTUALENVS_IN_PROJECT': '1',
        'PYTHONUNBUFFERED': '1',
        'PYTHONIOENCODING': 'UTF_8:replace'
    }
    if exists(env_file):
        env.update(parse_settings(env_file, env))

    first_time = False
    if not exists(join(virtualenv_path, "bin", "activate")):
        echo("-----> Creating virtualenv for '{}'".format(app), fg='green')
        try:
            makedirs(virtualenv_path)
        except FileExistsError:
            echo("-----> Env dir already exists: '{}'".format(app), fg='yellow')
        first_time = True

    if first_time or getmtime(requirements) > getmtime(virtualenv_path):
        echo("-----> Running poetry for '{}'".format(app), fg='green')
        call('poetry install', cwd=join(APP_ROOT, app), env=env, shell=True)

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
    """Deploy a containerized application using Podman"""
    
    app_path = join(APP_ROOT, app)
    env_file = join(APP_ROOT, app, 'ENV')
    env = {}
    
    if exists(env_file):
        env.update(parse_settings(env_file, env))
    
    echo("-----> Building container for '{}'".format(app), fg='green')
    call('podman build -t {app:s} .'.format(**locals()), cwd=app_path, shell=True)
    
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
        for w in v:
            unit_name = "{app:s}_{k:s}.{w:d}".format(**locals())
            unit_file = join(SYSTEMD_ROOT, unit_name + '.service')
            unit_link = join(user_systemd_dir, unit_name + '.service')
            
            if not exists(unit_file):
                echo("-----> Spawning '{app:s}:{k:s}.{w:d}'".format(**locals()), fg='green')
                spawn_worker(app, k, workers[k], env, w, unit_file)
                
                # Create symlink to user systemd directory if it doesn't exist
                if not exists(unit_link):
                    symlink(unit_file, unit_link)
                
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
                
                # Remove the symlink and unit file
                if exists(unit_link):
                    unlink(unit_link)
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
        # Static workers don't need a systemd unit
        return
    elif kind.startswith('cron'):
        # For cron-like jobs, use systemd timer instead of service
        timer_unit = unit_file.replace('.service', '.timer')
        # Parse the cron pattern from the command
        cron_parts = command.split(' ', 5)
        minute, hour, day, month, weekday, cmd = cron_parts
        
        # Convert cron pattern to systemd timer format
        # This is a simplified conversion and might need enhancement for complex patterns
        systemd_calendar = ''
        if weekday != '*':
            systemd_calendar += 'Sat' if weekday == '6' else 'Sun' if weekday == '0' else 'Mon' if weekday == '1' else 'Tue' if weekday == '2' else 'Wed' if weekday == '3' else 'Thu' if weekday == '4' else 'Fri'
        else:
            systemd_calendar += '*'
        
        systemd_calendar += ' ' + (month if month != '*' else '*')
        systemd_calendar += ' ' + (day if day != '*' else '*')
        systemd_calendar += ' ' + (hour if hour != '*' else '*')
        systemd_calendar += ' ' + (minute if minute != '*' else '*')
        
        # Create the timer unit
        timer_content = "[Unit]\n"
        timer_content += "Description=Timer for Piku app: {app} - {kind}\n\n".format(**locals())
        timer_content += "[Timer]\n"
        timer_content += "OnCalendar={}\n".format(systemd_calendar)
        timer_content += "Persistent=true\n\n"
        timer_content += "[Install]\n"
        timer_content += "WantedBy=timers.target\n"
        
        with open(timer_unit, 'w') as f:
            f.write(timer_content)
        
        # Update command to be the actual command part
        command = cmd
    
    # Check if this is a containerized app (Dockerfile present)
    containerized = exists(join(app_path, 'Dockerfile'))
    
    # Create systemd unit file
    unit_content = ''
    if containerized:
        # For containerized applications
        container_name = "{app}-{kind}-{ordinal}".format(**locals())
        image = app
        host_port = env.get('PORT', '8000')
        container_port = env.get('CONTAINER_PORT', host_port)
        
        # Parse any additional volume mounts
        volume_mounts = ''
        for key, value in env.items():
            if key.startswith('VOLUME_'):
                volume_mounts += '-v {}:{} \\\n    '.format(value.split(':')[0], value.split(':')[1])
        
        # Create environment arguments for docker run
        env_args = ''
        for key, value in env.items():
            if not key.startswith('VOLUME_') and key != 'PORT' and key != 'CONTAINER_PORT':
                env_args += '-e {}="{}" \\\n    '.format(key, value)
        
        # Format the environment variables for systemd
        environment_vars = '\n'.join(['Environment="{}={}"'.format(k, v) for k, v in env.items() 
                                     if not k.startswith('VOLUME_') and k != 'PORT' and k != 'CONTAINER_PORT'])
        
        unit_content = SYSTEMD_PODMAN_TEMPLATE.format(
            app_name=app,
            user=getpwuid(getuid()).pw_name,
            group=getgrgid(getgid()).gr_name,
            app_path=app_path,
            data_path=data_path,
            port=env.get('PORT', '8000'),
            environment_vars=environment_vars,
            image=image,
            container_name=container_name,
            host_port=host_port,
            container_port=container_port,
            volume_mounts=volume_mounts,
            env_args=env_args,
            log_path=log_file,
            command=command
        )
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
    
    # Find all systemd units for this app
    units = glob(join(SYSTEMD_ROOT, '{}*.service'.format(app)))
    
    if len(units) > 0:
        echo("Stopping app '{}'...".format(app), fg='yellow')
        for unit in units:
            unit_name = basename(unit)
            # Stop and disable the service
            call('systemctl --user stop {}'.format(unit_name), shell=True)
            call('systemctl --user disable {}'.format(unit_name), shell=True)
            
            # Remove symlink from user systemd directory
            unit_link = join(user_systemd_dir, unit_name)
            if exists(unit_link):
                unlink(unit_link)
    else:
        echo("Error: app '{}' not deployed or already stopped!".format(app), fg='red')


def do_restart(app):
    """Restarts a deployed app"""
    app = sanitize_app_name(app)
    user_systemd_dir = join(environ['HOME'], '.config', 'systemd', 'user')
    
    # Find all systemd units for this app
    units = glob(join(SYSTEMD_ROOT, '{}*.service'.format(app)))
    
    if len(units) > 0:
        echo("Restarting app '{}'...".format(app), fg='yellow')
        for unit in units:
            unit_name = basename(unit)
            # Restart the service
            call('systemctl --user restart {}'.format(unit_name), shell=True)
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


@group(context_settings=CONTEXT_SETTINGS)
def piku():
    """The smallest PaaS you've ever seen"""
    pass


piku.rc = getattr(piku, "result_callback", None) or getattr(piku, "resultcallback", None)


@piku.rc()
def cleanup(ctx):
    """Callback from command execution -- add debugging to taste"""
    pass

# --- User commands ---


@piku.command("apps")
def cmd_apps():
    """List apps, e.g.: piku apps"""
    apps = listdir(APP_ROOT)
    if not apps:
        echo("There are no applications deployed.")
        return

    for a in apps:
        units = glob(join(SYSTEMD_ROOT, '{}*.service'.format(a)))
        running = len(units) != 0
        echo(('*' if running else ' ') + a, fg='green')


@piku.command("config")
@argument('app')
def cmd_config(app):
    """Show config, e.g.: piku config <app>"""

    app = exit_if_invalid(app)

    config_file = join(ENV_ROOT, app, 'ENV')
    if exists(config_file):
        echo(open(config_file).read().strip(), fg='white')
    else:
        echo("Warning: app '{}' not deployed, no config found.".format(app), fg='yellow')


@piku.command("config:get")
@argument('app')
@argument('setting')
def cmd_config_get(app, setting):
    """e.g.: piku config:get <app> FOO"""

    app = exit_if_invalid(app)

    config_file = join(ENV_ROOT, app, 'ENV')
    if exists(config_file):
        env = parse_settings(config_file)
        if setting in env:
            echo("{}".format(env[setting]), fg='white')
    else:
        echo("Warning: no active configuration for '{}'".format(app))


@piku.command("config:set")
@argument('app')
@argument('settings', nargs=-1)
def cmd_config_set(app, settings):
    """e.g.: piku config:set <app> FOO=bar BAZ=quux"""

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


@piku.command("config:unset")
@argument('app')
@argument('settings', nargs=-1)
def cmd_config_unset(app, settings):
    """e.g.: piku config:unset <app> FOO"""

    app = exit_if_invalid(app)

    config_file = join(ENV_ROOT, app, 'ENV')
    env = parse_settings(config_file)
    for s in settings:
        if s in env:
            del env[s]
            echo("Unsetting {} for '{}'".format(s, app), fg='white')
    write_config(config_file, env)
    do_deploy(app)


@piku.command("config:live")
@argument('app')
def cmd_config_live(app):
    """e.g.: piku config:live <app>"""

    app = exit_if_invalid(app)

    live_config = join(ENV_ROOT, app, 'LIVE_ENV')
    if exists(live_config):
        echo(open(live_config).read().strip(), fg='white')
    else:
        echo("Warning: app '{}' not deployed, no config found.".format(app), fg='yellow')


@piku.command("deploy")
@argument('app')
def cmd_deploy(app):
    """e.g.: piku deploy <app>"""

    app = exit_if_invalid(app)
    do_deploy(app)


@piku.command("destroy")
@argument('app')
def cmd_destroy(app):
    """e.g.: piku destroy <app>"""

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


@piku.command("logs")
@argument('app')
@argument('process', nargs=1, default='*')
def cmd_logs(app, process):
    """Tail running logs, e.g: piku logs <app> [<process>]"""

    app = exit_if_invalid(app)

    logfiles = glob(join(LOG_ROOT, app, process + '.*.log'))
    if len(logfiles) > 0:
        for line in multi_tail(app, logfiles):
            echo(line.strip(), fg='white')
    else:
        echo("No logs found for app '{}'.".format(app), fg='yellow')


@piku.command("ps")
@argument('app')
def cmd_ps(app):
    """Show process count, e.g: piku ps <app>"""

    app = exit_if_invalid(app)

    config_file = join(ENV_ROOT, app, 'SCALING')
    if exists(config_file):
        echo(open(config_file).read().strip(), fg='white')
    else:
        echo("Error: no workers found for app '{}'.".format(app), fg='red')


@piku.command("ps:scale")
@argument('app')
@argument('settings', nargs=-1)
def cmd_ps_scale(app, settings):
    """e.g.: piku ps:scale <app> <proc>=<count>"""

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


@piku.command("run")
@argument('app')
@argument('cmd', nargs=-1)
def cmd_run(app, cmd):
    """e.g.: piku run <app> ls -- -al"""

    app = exit_if_invalid(app)

    config_file = join(ENV_ROOT, app, 'LIVE_ENV')
    environ.update(parse_settings(config_file))
    for f in [stdout, stderr]:
        fl = fcntl(f, F_GETFL)
        fcntl(f, F_SETFL, fl | O_NONBLOCK)
    p = Popen(' '.join(cmd), stdin=stdin, stdout=stdout, stderr=stderr, env=environ, cwd=join(APP_ROOT, app), shell=True)
    p.communicate()


@piku.command("restart")
@argument('app')
def cmd_restart(app):
    """Restart an app: piku restart <app>"""

    app = exit_if_invalid(app)

    do_restart(app)


@piku.command("stop")
@argument('app')
def cmd_stop(app):
    """Stop an app, e.g: piku stop <app>"""
    app = exit_if_invalid(app)
    do_stop(app)


@piku.command("setup")
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

    # Check for required binaries
    requirements = ['caddy', 'podman', 'systemctl']
    missing = []
    for req in requirements:
        if not which(req):
            missing.append(req)
    
    if missing:
        echo("Warning: Missing required binaries: {}".format(', '.join(missing)), fg='yellow')
        echo("You'll need to install these packages before using Piku", fg='yellow')

    # mark this script as executable (in case we were invoked via interpreter)
    if not (stat(PIKU_SCRIPT).st_mode & S_IXUSR):
        echo("Setting '{}' as executable.".format(PIKU_SCRIPT), fg='yellow')
        chmod(PIKU_SCRIPT, stat(PIKU_SCRIPT).st_mode | S_IXUSR)


@piku.command("setup:ssh")
@argument('public_key_file')
def cmd_setup_ssh(public_key_file):
    """Set up a new SSH key (use - for stdin)"""

    def add_helper(key_file):
        if exists(key_file):
            try:
                fingerprint = str(check_output('ssh-keygen -lf ' + key_file, shell=True)).split(' ', 4)[1]
                key = open(key_file, 'r').read().strip()
                echo("Adding key '{}'.".format(fingerprint), fg='white')
                setup_authorized_keys(fingerprint, PIKU_SCRIPT, key)
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


@piku.command("update")
def cmd_update():
    """Update the piku cli"""
    echo("Updating piku...")

    with NamedTemporaryFile(mode="w") as f:
        tempfile = f.name
        cmd = """curl -sL -w %{{http_code}} {} -o {}""".format(PIKU_RAW_SOURCE_URL, tempfile)
        response = check_output(cmd.split(' '), stderr=STDOUT)
        http_code = response.decode('utf8').strip()
        if http_code == "200":
            copyfile(tempfile, PIKU_SCRIPT)
            echo("Update successful.")
        else:
            echo("Error updating piku - please check if {} is accessible from this machine.".format(PIKU_RAW_SOURCE_URL))
    echo("Done.")


# --- Internal commands ---

@piku.command("git-hook")
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


@piku.command("git-receive-pack")
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
cat | PIKU_ROOT="{PIKU_ROOT:s}" {PIKU_SCRIPT:s} git-hook {app:s}""".format(**env))
        # Make the hook executable by our user
        chmod(hook_path, stat(hook_path).st_mode | S_IXUSR)
    # Handle the actual receive. We'll be called with 'git-hook' after it happens
    call('git-shell -c "{}" '.format(argv[1] + " '{}'".format(app)), cwd=GIT_ROOT, shell=True)


@piku.command("git-upload-pack")
@argument('app')
def cmd_git_upload_pack(app):
    """INTERNAL: Handle git upload pack for an app"""
    app = sanitize_app_name(app)
    env = globals()
    env.update(locals())
    # Handle the actual receive. We'll be called with 'git-hook' after it happens
    call('git-shell -c "{}" '.format(argv[1] + " '{}'".format(app)), cwd=GIT_ROOT, shell=True)


@piku.command("scp", context_settings=dict(ignore_unknown_options=True, allow_extra_args=True))
@pass_context
def cmd_scp(ctx):
    """Simple wrapper to allow scp to work."""
    call(" ".join(["scp"] + ctx.args), cwd=GIT_ROOT, shell=True)


@piku.command("help")
@pass_context
def cmd_help(ctx):
    """display help for piku"""
    echo(ctx.parent.get_help())


def _get_plugin_commands(path):
    sys_path.append(abspath(path))

    cli_commands = []
    if isdir(path):
        for item in listdir(path):
            module_path = join(path, item)
            if isdir(module_path):
                try:
                    module = import_module(item)
                except Exception:
                    module = None
                if hasattr(module, 'cli_commands'):
                    cli_commands.append(module.cli_commands())

    return cli_commands


if __name__ == '__main__':
    cli_commands = _get_plugin_commands(path=PIKU_PLUGIN_ROOT)
    cli_commands.append(piku)
    cli = CommandCollection(sources=cli_commands)
    cli()