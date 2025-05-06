# Piku: Micro-PaaS Specification

## Overview

Piku is a lightweight Platform as a Service (PaaS) implementation designed for small-scale deployments on a single server. It provides a Heroku-like experience with git-based deployments, process management, and support for multiple programming languages and frameworks.

## Core Architecture

### Components

1. **Git-based deployment system**
   - Uses git hooks to trigger deployments
   - Supports standard git workflows (push to deploy)

2. **Process Management**
   - Uses uWSGI Emperor for process supervision
   - Supports multiple process types defined in Procfile
   - Manages worker processes and scales them as needed

3. **Web Server Integration**
   - Nginx configuration generation and management
   - SSL/TLS support with automatic certificate management via acme.sh
   - Static file serving and proxy configuration

4. **Runtime Environment Management**
   - Language-specific virtual environments
   - Environment variable management
   - Support for multiple programming languages/frameworks

## Directory Structure

Piku organizes its files in the following directory structure under `$HOME/.piku`:

- `apps/` - Application code
- `data/` - Persistent data for applications
- `envs/` - Virtual environments for applications
- `repos/` - Git repositories
- `logs/` - Application logs
- `nginx/` - Nginx configuration files
- `cache/` - Cache files for applications
- `uwsgi-available/` - Available uWSGI configurations
- `uwsgi-enabled/` - Enabled uWSGI configurations
- `uwsgi/` - uWSGI runtime files
- `acme/` - ACME challenge files for SSL certificates
- `plugins/` - Piku plugins

## Supported Runtimes

Piku detects and supports the following application types:

1. **Python**
   - Standard virtualenv with requirements.txt
   - Poetry support with pyproject.toml
   - uv support with pyproject.toml

2. **Ruby**
   - Bundler support via Gemfile

3. **Node.js**
   - npm/package.json support
   - Custom package managers via NODE_PACKAGE_MANAGER env var

4. **Java**
   - Maven support (pom.xml)
   - Gradle support (build.gradle)

5. **Go**
   - Golang support (Godeps, go.mod)

6. **Clojure**
   - Leiningen support (project.clj)
   - Clojure CLI support (deps.edn)

7. **Rust**
   - Cargo support (Cargo.toml, rust-toolchain.toml)

8. **PHP**
   - PHP application support via uwsgi_php plugin

9. **Generic**
   - Support for any application with a Procfile

## Runtime Detection and Deployment

Piku uses a cascading detection system to identify application types:

1. **Detection Order:**
   - First checks for language-specific files (requirements.txt, package.json, etc.)
   - Falls back to worker types in Procfile (php, web, static, etc.)
   - Each detection adds environment variables specific to that runtime

2. **Detection Mechanism:**
   - Python: Checks for `requirements.txt` or `pyproject.toml`
   - Ruby: Checks for `Gemfile`
   - Node.js: Checks for `package.json`
   - Java Maven: Checks for `pom.xml`
   - Java Gradle: Checks for `build.gradle`
   - Go: Checks for `Godeps`, `go.mod` or any `*.go` files
   - Clojure: Checks for `deps.edn` (CLI) or `project.clj` (Leiningen)
   - Rust: Checks for `Cargo.toml` AND `rust-toolchain.toml`
   - PHP: Identified by the presence of a `php` worker type in Procfile
   - Generic: Identified by presence of both `web` and `release` workers
   - Static: Identified by presence of a `static` worker in Procfile

3. **Priority Rules:**
   - WSGI applications (wsgi, jwsgi, rwsgi) take precedence over web workers
   - If both are found, web worker is automatically disabled

## Configuration

### Environment Variables

Piku uses the following files for configuration:

- `ENV` file in the application root for default settings
- `ENV` file in the environment directory for customized settings
- `LIVE_ENV` file generated during deployment for runtime settings

### Scaling Configuration

- `SCALING` file in the environment directory for process scaling

### Application Process Types

Applications define their process types in a `Procfile` with the following format:

```
<process_type>: <command>
```

Special process types:

- `web`: HTTP service (port will be automatically assigned)
- `wsgi`: Python WSGI application
- `jwsgi`: Java WSGI application
- `rwsgi`: Ruby WSGI application
- `static`: Static file server
- `php`: PHP application
- `cron-*`: Scheduled tasks
- `release`: Commands run at release time
- `preflight`: Commands run before deployment

## Worker Process Types and Handling

Piku supports several specialized worker types, each handled differently:

- `web`: Runs as an attach-daemon under uWSGI, proxied via Nginx
- `wsgi`: Python WSGI application run directly by uWSGI
  - For Python 2: Uses the 'python' plugin
  - For Python 3: Uses the 'python3' plugin
  - Supports gevent (Python 2) or asyncio (Python 2/3) for async operations
- `jwsgi`: Java WSGI application with 'jvm' and 'jwsgi' plugins
- `rwsgi`: Ruby WSGI application with 'rack', 'rbrequire', and 'post-buffering' plugins
- `php`: PHP application via uwsgi_php plugin
- `static`: Static file server via Nginx only (no uWSGI process)
- `cron-*`: Scheduled tasks using uWSGI's built-in cron functionality
- Regular workers: Run as attach-daemon processes under uWSGI

Worker scaling is controlled through the SCALING file, which maps process types to instance counts.

## Nginx Configuration

Piku automatically generates Nginx configuration based on environment variables:

- `NGINX_SERVER_NAME`: Hostname(s) for the application (comma-separated values)
- `NGINX_STATIC_PATHS`: Static file mappings in format `/prefix1:path1,/prefix2:path2`
- `NGINX_HTTPS_ONLY`: Force HTTPS (true/false)
- `NGINX_CACHE_PREFIXES`: URL paths to cache in format `prefix1|prefix2|prefix3`
- `NGINX_CACHE_SIZE`: Cache size in gigabytes (default: 1)
- `NGINX_CACHE_TIME`: Cache duration for content in seconds (default: 3600)
- `NGINX_CACHE_CONTROL`: Cache control header timeout in seconds (default: 3600)
- `NGINX_CACHE_REDIRECTS`: Cache duration for redirects in seconds (default: 3600)
- `NGINX_CACHE_ANY`: Cache duration for other responses in seconds (default: 3600)
- `NGINX_CACHE_EXPIRY`: Cache expiry time in seconds (default: 86400)
- `NGINX_CACHE_PATH`: Custom cache directory path (defaults to `$HOME/.piku/cache/<app>`)
- `NGINX_CLOUDFLARE_ACL`: Restrict access to CloudFlare IP addresses (true/false)
- `NGINX_ALLOW_GIT_FOLDERS`: Allow access to .git folders (true/false)
- `NGINX_INCLUDE_FILE`: Custom Nginx config include file path
- `NGINX_IPV4_ADDRESS`: IPv4 bind address (defaults to 0.0.0.0)
- `NGINX_IPV6_ADDRESS`: IPv6 bind address (defaults to [::])
- `NGINX_CATCH_ALL`: Catch-all URL for static paths
- `DISABLE_IPV6`: Disable IPv6 support (true/false)

## SSL/TLS Certificate Management

Piku manages SSL/TLS certificates through the following process:

1. When `NGINX_SERVER_NAME` is set, Piku attempts to secure the app with HTTPS
2. It first checks for an existing certificate in the ACME_ROOT directory
3. If acme.sh is installed, it tries to obtain certificates from Let's Encrypt:
   - Creates a temporary Nginx config to serve the ACME challenge
   - Uses acme.sh to request certificates for all domains in NGINX_SERVER_NAME
   - Installs certificates to the Nginx directory
4. If certificate acquisition fails or acme.sh isn't available, it generates a self-signed certificate
5. For HTTPS-only mode (`NGINX_HTTPS_ONLY=true`), all HTTP requests are redirected to HTTPS

The system supports HTTP/2 if Nginx was compiled with that module, or SPDY as fallback.

## CloudFlare Integration

When `NGINX_CLOUDFLARE_ACL` is set to true, Piku:

1. Retrieves the official CloudFlare IP ranges from their API
2. Configures Nginx to allow access only from those IP addresses
3. Additionally allows access from the client's IP address at deploy time
4. Sets up Nginx to recognize the `CF-Connecting-IP` header for proper IP logging
5. Blocks all other traffic with `deny all` directive

This provides an additional layer of security by ensuring that traffic only comes through CloudFlare's network.

## Static File Optimization

For static file serving, Piku configures Nginx with optimized settings:

- Enables `sendfile` for efficient file serving
- Sets `sendfile_max_chunk` to 1MB to optimize throughput
- Enables TCP NOPUSH for reducing packet count
- Configures `directio` (8MB) and `aio threads` for improved I/O performance
- Implements `try_files` with fallback to catch-all URLs where configured

If a `static` worker is defined in the Procfile, its path is automatically added to the static mappings.

## uWSGI Configuration

uWSGI settings can be customized via environment variables:

- `UWSGI_PROCESSES`: Number of processes (default: 1)
- `UWSGI_THREADS`: Number of threads (default: 4 for WSGI processes)
- `UWSGI_LISTEN`: Listen queue size (default: 16)
- `UWSGI_MAX_REQUESTS`: Maximum requests per worker (default: 1024)
- `UWSGI_IDLE`: Idle timeout for workers (enables on-demand workers with "cheap" and "die-on-idle" options)
- `UWSGI_GEVENT`: Enable gevent for Python 2 applications
- `UWSGI_ASYNCIO`: Enable asyncio for Python applications (specify number of async tasks)
- `UWSGI_ENABLE_THREADS`: Enable threads support (true/false, default: true)
- `UWSGI_LOG_X_FORWARDED_FOR`: Log X-Forwarded-For header (true/false, default: false)
- `UWSGI_LOG_MAXSIZE`: Maximum log file size (default: 1048576 bytes)
- `UWSGI_INCLUDE_FILE`: Custom uWSGI config include file

## PHP Configuration

For PHP applications, Piku configures uWSGI with the following settings:

- Uses the `http` and `php` plugins
- Automatically configures document root based on the Procfile's `php` command path
- Sets up static file handling with `check-static` and appropriate skip extensions
- Configures PHP specific settings:
  - `php-docroot`: Document root directory for PHP files
  - `php-allowed-ext`: File extensions allowed to be processed by PHP (default: `.php`)
  - `php-index`: Default index file (default: `index.php`)
  - `static-index`: Default static index file (default: `index.html`)
  - `static-skip-ext`: File extensions to skip for static serving (`.php`, `.inc`)

PHP applications are detected when a `php` worker is defined in the Procfile.

## Socket Handling

Piku automatically manages socket connections between Nginx and uWSGI:

1. For WSGI applications (Python, Java, Ruby):
   - When `NGINX_SERVER_NAME` is set, communication happens via Unix sockets:
     - Socket file is created at `$HOME/.piku/nginx/<app>.sock`
     - Socket permissions are set to 664 for proper access
     - Nginx is configured to use `uwsgi_pass` to the Unix socket
   - Without `NGINX_SERVER_NAME`, uWSGI binds to TCP port:
     - Uses `http`, `http-use-socket`, and `http-socket` directives
     - Binds to the address specified by `BIND_ADDRESS` (defaults to 127.0.0.1)

2. For web applications:
   - uWSGI runs them as `attach-daemon` processes
   - Nginx proxies requests to their bound TCP port
   - Proxy configuration includes WebSocket upgrade headers

3. For PHP applications:
   - uWSGI runs with `http` plugin binding to the assigned PORT
   - Static files are handled directly by uWSGI with `check-static`

4. For static applications:
   - No uWSGI process is created
   - Nginx serves files directly from the specified path

## Language-Specific Configuration

### Node.js

Node.js applications can be configured with additional environment variables:

- `NODE_VERSION`: Specify the Node.js version to use (requires nodeenv)
- `NODE_PACKAGE_MANAGER`: Specify an alternative package manager (default: "npm --package-lock=false")
- `NODE_PATH`: Path to node modules
- `NPM_CONFIG_PREFIX`: NPM prefix configuration

### Node.js Environment Management

Piku provides advanced Node.js environment management:

1. **Version Management:**
   - Uses `nodeenv` to create isolated Node.js environments
   - Supports specific Node.js versions via `NODE_VERSION` environment variable
   - Prevents version changes while the application is running
   - Uses prebuilt binaries for faster installation (`--prebuilt` flag)

2. **Package Management:**
   - Creates a dedicated `node_modules` directory in the environment path
   - Symlinks this directory to the application root for compatibility
   - Supports custom package managers via `NODE_PACKAGE_MANAGER` environment variable
   - Default package manager is `npm --package-lock=false`
   - Automatically installs alternative package managers via npm if specified
   - Copies package.json to the environment directory to ensure consistent installations

3. **Environment Integration:**
   - Adds Node.js bin directories to the PATH automatically
   - Sets NODE_PATH to enable proper module resolution
   - Configures NPM_CONFIG_PREFIX to maintain isolation between applications

### Python

Python applications can be configured with additional environment variables:

- `PYTHON_VERSION`: Python version to use (default: "3")
- `PYTHONUNBUFFERED`: Set to "1" for unbuffered output (default)
- `PYTHONIOENCODING`: Set to "UTF_8:replace" for readable UTF-8 mapping (default)

### Python Environment Management

Piku supports multiple Python package management approaches:

1. **Standard Virtualenv + requirements.txt:**
   - Creates a virtualenv using the Python version specified in PYTHON_VERSION (defaults to 3)
   - Installs requirements from requirements.txt using pip
   - Activates the environment during deployment using activate_this.py
   - Rebuilds/updates if requirements.txt has changed since last deployment

2. **Poetry:**
   - Experimental support for Poetry projects with pyproject.toml
   - Sets POETRY_VIRTUALENVS_IN_PROJECT=1 to keep virtualenv in project
   - Creates a .venv symlink in app directory pointing to the environment path
   - Uses poetry install for dependency management
   - Inherits all environment variables from the deployment environment

3. **UV:**
   - Experimental support for uv package manager with pyproject.toml
   - Sets UV_PROJECT_ENVIRONMENT to point to the environment path
   - Uses uv sync with --python-preference only-system flag
   - Compatible with any Python project using pyproject.toml

### Go Environment Management

Piku offers specific handling for Go applications:

1. **Environment Setup:**
   - Creates a dedicated GOPATH in the environment directory
   - If available, copies a pre-built GOPATH structure to save provisioning time
   - Handles both older Godeps-style dependencies and modern go.mod projects

2. **Dependency Management:**
   - Detects changes in Godeps directory and runs `godep update` when needed
   - For go.mod projects, runs `go mod tidy` to ensure dependencies are up to date
   - Sets appropriate Go environment variables (GOPATH, GOROOT, GO15VENDOREXPERIMENT)

### Ruby Environment Management

Piku provides a streamlined approach for Ruby applications:

1. **Environment Setup:**
   - Creates a dedicated directory for the Ruby application's dependencies
   - Sets up proper Ruby environment variables and PATH
   - Uses Bundler to manage gems in an isolated environment

2. **Dependency Management:**
   - Configures Bundler to store gems in the application's environment path
   - Uses `bundle config set --local path $VIRTUAL_ENV` for isolation
   - Runs `bundle install` to install or update dependencies
   - Preserves existing environments during rebuilds

### Java Environment Management

Piku provides two approaches for Java applications:

1. **Maven Projects:**
   - Sets up a dedicated path for Java applications in the environment directory
   - Automatically runs `mvn package` for first-time deployments
   - Uses `mvn clean package` for subsequent deployments
   - Detects changes by checking for the presence of the target directory
   - A TODO in the code suggests future jenv integration for Java version isolation

2. **Gradle Projects:**
   - Sets up dedicated environment directory similarly to Maven projects
   - Runs `gradle build` for first-time deployments
   - Uses `gradle clean build` for subsequent deployments

### Clojure Environment Management

Piku supports two Clojure build systems:

1. **Clojure CLI (deps.edn):**
   - Creates a dedicated environment directory
   - Sets CLJ_CONFIG to reference user's .clojure directory or custom location
   - Uses `clojure -T:build release` to build the application

2. **Leiningen (project.clj):**
   - Creates a dedicated environment directory
   - Sets LEIN_HOME to reference user's .lein directory or custom location
   - Uses the sequence of `lein clean` followed by `lein uberjar` for builds

### Rust Environment Management

Piku provides basic support for Rust applications:

1. **Project Requirements:**
   - Requires both `Cargo.toml` and `rust-toolchain.toml` files to be detected as a Rust application
   - Uses standard Cargo build process

2. **Build Process:**
   - Simply runs `cargo build` in the application directory
   - Does not create a separate environment directory like other runtimes
   - Relies on Cargo's built-in dependency management

### Other Runtime Settings

- `PIKU_AUTO_RESTART`: Automatically restart application on deployment (true/false, default: true)
- `BIND_ADDRESS`: Address to bind application to (default: 127.0.0.1)
- `PORT`: Port to bind application to (automatically assigned if not specified)

## SSH Key Management and Git Integration

Piku uses SSH as its primary mechanism for secure deployments and management:

### SSH Key Setup

1. **Authorized Keys Configuration**
   - Piku adds entries to the user's `~/.ssh/authorized_keys` file
   - Each key is configured with specific restrictions:
     - `command="FINGERPRINT={fingerprint} NAME=default {piku_script} $SSH_ORIGINAL_COMMAND"`
     - `no-agent-forwarding,no-user-rc,no-X11-forwarding,no-port-forwarding`
   - The SSH fingerprint is captured and passed as an environment variable to Piku
   - All SSH commands are forced through the Piku script for security

2. **SSH Command Handling**
   - The setup:ssh command accepts a path to a public key file or '-' for stdin
   - Keys are validated before being added (invalid keys are rejected)
   - The fingerprint is extracted using ssh-keygen

### Git Push Deployment Flow

1. **Repository Initialization**
   - When a user first pushes to a non-existent app, Piku:
     - Creates a bare Git repository in `$HOME/.piku/repos/<app>`
     - Sets up a post-receive hook that triggers the Piku git-hook command
     - Makes the hook executable with appropriate permissions

2. **Git Commands Handling**
   - Piku handles three git commands:
     - `git-receive-pack`: Processes incoming git pushes
     - `git-upload-pack`: Handles git pulls and fetches
     - `git-hook`: Post-receive hook that triggers the actual deployment

3. **Deployment Process**
   - Upon receiving a push, the post-receive hook:
     - Reads the oldrev, newrev, and ref from stdin
     - Creates the application directory if it doesn't exist
     - Clones the repository to the app directory
     - Triggers the deployment process with the newrev

4. **Security Considerations**
   - All git operations are handled through git-shell for security
   - The commands and their options are strictly controlled
   - Repository access is limited to the specific app being pushed

5. **SCP Support**
   - Piku also provides an SCP wrapper to allow secure file copying
   - This uses the same authentication mechanism as git pushes

## Command Line Interface

Piku provides a CLI with the following commands:

### Application Management

- `piku apps`: List applications
- `piku deploy <app>`: Deploy an application
- `piku destroy <app>`: Destroy an application
- `piku restart <app>`: Restart an application
- `piku stop <app>`: Stop an application
- `piku ps <app>`: Show process information
- `piku ps:scale <app> <proc>=<count>`: Scale processes

### Configuration

- `piku config <app>`: Show configuration
- `piku config:get <app> <setting>`: Get a configuration value
- `piku config:set <app> <key>=<value> [...]`: Set configuration values
- `piku config:unset <app> <key> [...]`: Unset configuration values
- `piku config:live <app>`: Show live configuration

### Logging

- `piku logs <app> [<process>]`: View application logs

### Utilities

- `piku run <app> <cmd>`: Run a command in the application environment
- `piku setup`: Initialize the Piku environment
- `piku setup:ssh <public_key_file>`: Set up SSH keys for deployment
- `piku update`: Update the Piku CLI
- `piku help`: Display help for piku commands

### Internal Commands

- `piku git-hook <app>`: Git post-receive hook handler
- `piku git-receive-pack <app>`: Git push handler
- `piku git-upload-pack <app>`: Git pull handler
- `piku scp`: SCP wrapper

## Deployment Process

1. User pushes code to the Piku server via git
2. The git-hook is triggered
3. Piku detects the application type based on files in the repository
4. Piku creates or updates the virtual environment/dependencies
5. Piku generates uWSGI and Nginx configurations
6. Piku starts the application processes

## Security

- SSH key-based authentication for git deployments
- Uses Unix permissions for file isolation
- Supports SSL/TLS via Let's Encrypt integration
- CloudFlare IP restrictions option

## Log Management

Piku configures comprehensive logging for all applications:

1. **Log File Management:**
   - Log files are stored in `$HOME/.piku/logs/<app>/<worker_type>.<instance_number>.log`
   - Log rotation is handled by uWSGI with backup files named `.old`
   - Log permissions are set to 640 with proper user/group ownership
   - Maximum log size is controlled via `UWSGI_LOG_MAXSIZE` (default: 1048576 bytes)

2. **Log Formats:**
   - WSGI and web processes use a detailed log format that includes:
     - Client address
     - Username
     - Timestamp in local time
     - HTTP method, URI, and protocol
     - Status code
     - Response size
     - Referer
     - User agent
     - Response time in milliseconds
   - The `logs` command can tail multiple log files with process type prefixes

3. **Custom Logging:**
   - X-Forwarded-For logging can be enabled with `UWSGI_LOG_X_FORWARDED_FOR`
   - When CloudFlare integration is active, the `CF-Connecting-IP` header is properly mapped

## Limitations

- Designed for single-server deployments
- No built-in clustering or high-availability features
- Single user model (runs under the same Unix user)
