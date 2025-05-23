# kata

Kata is a tool for deploying and managing applications using systemd, Podman, and Caddy. It simplifies the process of creating, updating, and destroying application instances while providing a consistent environment for development and production, and is a direct descendant of `piku`.

> Kata (型), meaning "form," "model," or "pattern." This aligns with the structured approach of systemd unit files, Dockerfiles/Quadlets for Podman, and Caddyfiles, representing the defined "forms" for deploying and managing applications

# Requirements

`kata` requires a system with `systemd`, `podman`, and `caddy` installed. It is designed to work with the following versions:

- `systemd`: 239 or later
- `podman`: 4.0 or later
- `caddy`: 2.4 or later
- `podman-compose`: 1.0 or later

This means that Debian 13 (Trixie) or later is a requirement. Additionally, in `trixie` you should `systemctl enable caddy-api` to make sure Caddy is started with API config persistence and make sure to log in to the `kata` account using `sudo machinectl shell kata@` if you need to do some debugging.

## Caddy Configuration

Kata uses Caddy's API to configure routing and proxying for your applications. To customize how Caddy handles your application, create a `caddy.json` file in your application's root directory.

The `caddy.json` file supports environment variable substitution. Variables like `$PORT`, `$BIND_ADDRESS`, or any other environment variable defined in your application's ENV file can be used in the configuration.

### Application Structure

When you deploy an app with kata, it's organized as follows:

- **App Code**: `~/.kata/apps/your-app` - Your application's code is deployed here from your git repository
- **Data Directory**: `~/.kata/data/your-app` - Persistent data storage that survives new deployments
- **Environment Settings**: `~/.kata/envs/your-app/ENV` - Environment variables
- **Logs**: `~/.kata/logs/your-app` - Log files for your application
- **Cache**: `~/.kata/cache/your-app` - Cache storage

Your application's Procfile, ENV file, and caddy.json should be placed in the app code directory (they are typically part of your git repository).

## Sample caddy.json Files

### 1. Static File Serving

Serve static files from a directory:

```json
{
  "listen": [":$PORT"],
  "routes": [
    {
      "handle": [
        {
          "handler": "file_server",
          "root": "$APP_ROOT",
          "index_names": ["index.html", "index.htm"]
        }
      ]
    }
  ]
}

```

This replaces the `static` worker in a `piku` Procfile.

> **Note:** Use `$APP_ROOT` when your static files are part of your git repository. Use `$DATA_ROOT` when files are generated or uploaded separately from deployments.

### 2. Basic HTTP Proxy

Proxy requests to your application:

```json
{
  "listen": [":$PORT"],
  "routes": [
    {
      "handle": [
        {
          "handler": "reverse_proxy",
          "upstreams": [
            {
              "dial": "$BIND_ADDRESS:$PORT"
            }
          ],
          "headers": {
            "request": {
              "add": {
                "X-Forwarded-Proto": ["http"],
                "X-Real-IP": ["{remote}"],
                "X-Forwarded-For": ["{remote}"]
              }
            }
          }
        }
      ]
    }
  ]
}
```

### 3. HTTPS Configuration

Host your application with automatic HTTPS (requires a public domain and port 443 access):

```json
{
  "listen": [":443"],
  "routes": [
    {
      "match": [
        {
          "host": ["$DOMAIN_NAME"]
        }
      ],
      "handle": [
        {
          "handler": "reverse_proxy",
          "upstreams": [
            {
              "dial": "$BIND_ADDRESS:$PORT"
            }
          ],
          "headers": {
            "request": {
              "add": {
                "X-Forwarded-Proto": ["{http.request.scheme}"],
                "X-Real-IP": ["{remote}"],
                "X-Forwarded-For": ["{remote}"]
              }
            }
          }
        }
      ]
    }
  ],
  "tls_connection_policies": [
    {
      "match": {
        "sni": ["$DOMAIN_NAME"]
      }
    }
  ],
  "automatic_https": {
    "disable": false
  }
}
```

### 4. Advanced Configuration (Static Assets with Cache)

Serve a web application with static assets and caching:

```json
{
  "listen": [":$PORT"],
  "routes": [
    {
      "match": [
        {
          "host": ["$DOMAIN_NAME"]
        }
      ],
      "handle": [
        {
          "handler": "vars",
          "root": "$APP_ROOT/static"
        },
        {
          "handler": "file_server",
          "match": [
            {
              "path": ["/static/*"]
            }
          ],
          "root": "{http.vars.root}",
          "index_names": ["index.html"],
          "headers": {
            "response": {
              "set": {
                "Cache-Control": ["public, max-age=3600"]
              }
            }
          }
        },
        {
          "handler": "reverse_proxy",
          "upstreams": [
            {
              "dial": "$BIND_ADDRESS:$PORT"
            }
          ]
        }
      ]
    }
  ]
}
```

## Environment Variables

Make sure to set these key environment variables in your application's ENV file:

- `PORT`: Required. The port your application listens on.
- `BIND_ADDRESS`: The address to bind to (defaults to 127.0.0.1).
- `DOMAIN_NAME`: For HTTPS configurations, set this to your domain name.



You can also use these kata-specific variables in your caddy.json:

- `$APP_ROOT`: App directory path (typically ~/.kata/apps/your-app) - use for files in your git repository
- `$DATA_ROOT`: App data directory (typically ~/.kata/data/your-app) - use for persistent data that survives deployments
- `$LOG_ROOT`: Log directory (typically ~/.kata/logs/your-app)
- `$CACHE_ROOT`: Cache directory (typically ~/.kata/cache/your-app)

For more detailed Caddy configurations, refer to the [Caddy JSON documentation](https://caddyserver.com/docs/json/).

### Caddy API Integration

Kata interacts with Caddy through its admin API, which by default runs on `localhost:2019`. When you deploy an app, if a `caddy.json` file is present:

1. The file is loaded and environment variables are expanded
2. The current complete Caddy configuration is retrieved from the API
3. Your app's configuration is updated within the complete configuration structure, preserving all other settings
4. The entire updated configuration is sent to Caddy's `/load` endpoint
5. When an app is destroyed, its configuration is similarly removed from the complete configuration while preserving all other settings

Make sure Caddy is running with its admin API enabled. The default configuration in kata assumes Caddy is started with:

```caddyfile
{
  debug
  admin localhost:2019
}
```
