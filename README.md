# kata

> Kata (型), meaning "form," "model," or "pattern." 

`kata` is a tool for deploying and managing applications and is a direct descendant of `piku`. It is _not_ a replacement for `piku`, but rather a playground for experimenting with new features and improvements, because `piku` is meant to be stable and reliable for production use. Renaming it also makes it easier for both to coexist without confusion.

`kata` is also a set of repetitive patterns and has multiple branches that can be used to experiment with different features and improvements. Some of those features may eventually be merged into `piku`, while others may remain as experimental branches in `kata`.

In short, this is not production-ready software, but rather a playground for experimenting with new features and improvements. If you are looking for a stable and reliable tool for deploying and managing applications, please use `piku` instead.

Right now there are two branches of `kata`:

- `systemd`: This branch is designed to work with `systemd` and `caddy` and to explore replacing `uwsgi` with `systemd` services for application management as well as replacing `nginx` with `caddy` for routing and proxying. This tries to maintain the `Procfile` and `ENV` file structure of `piku`.
- `compose`: This is the "anti-`piku`" branch, which is designed to work with `docker` and `docker stack` and to explore replacing `uwsgi` with `docker` containers for application management as well as replacing `nginx` with `caddy` for routing and proxying. This branch is an exploration because `podman` is still (in 2025) very brittle, but fleshing out the idea of using `docker` containers for application management is a good exercise in figuring out how to later switch to `podman` and `podman` quadlets together with `systemd`. This uses a single YAML file to define the application and its environment, which is a departure from the `Procfile` and `ENV` file structure of `piku` but makes it easier to manage complex configurations, and by leveraging `docker swarm` mode it can also provide more secure secret management and isolation.

# Requirements


Right now, Debian 13 (Trixie) or later is a requirement because it is likely to be the least common denominator for packaging `caddy` and other components in Linux distributions.

Additionally, in `trixie` you should `systemctl enable caddy-api` to make sure `caddy` is started with API config persistence and make sure to log in to the `kata` account using `sudo machinectl shell kata@` if you need to do some debugging.

The default `caddy` configuration should be set to:

```caddyfile
{
  debug
  admin localhost:2019
}
```
