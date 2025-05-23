# Installation Guide for Kata

This guide provides instructions on how to install and set up Kata on a Debian-based system.

## Prerequisites

Before installing Kata, ensure your Debian system has the following packages installed.

```bash
sudo apt update
sudo apt install -y python3 python3-pip python3-venv git curl uidmap openssh-server systemd-container podman podman-compose caddy
```

**Minimum Python Version:** Kata requires Python 3.10 or higher.

### Install Caddy

Caddy is used as the web server. Follow the official Caddy documentation to install it, as it's typically not in the default Debian repositories. Instructions can usually be found at [https://caddyserver.com/docs/install](https://caddyserver.com/docs/install).

A common way to install Caddy on Debian is:

```bash
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https
curl -1sLf '''https://dl.cloudsmith.io/public/caddy/stable/gpg.key''' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf '''https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt''' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update
sudo apt install -y caddy
```

### Install Podman

Podman is used for running containerized applications.

```bash
sudo apt install -y podman
```
For rootless Podman, ensure your user has appropriate subuid and subgid ranges:
```bash
sudo usermod --add-subuids 100000-165535 --add-subgids 100000-165535 $(whoami)
podman system migrate
```

### Install Optional Python Build Tools

Depending on your Python application's packaging, you might need `poetry` or `uv`.

**Poetry:**
Follow the official Poetry installation guide: [https://python-poetry.org/docs/#installation](https://python-poetry.org/docs/#installation)
```bash
curl -sSL https://install.python-poetry.org | python3 -
```
Ensure `~/.local/bin` is in your `PATH`. Add this to your `~/.bashrc` or `~/.zshrc`:
`export PATH="$HOME/.local/bin:$PATH"`

**uv:**
Follow the official uv installation guide: [https://github.com/astral-sh/uv#installation](https://github.com/astral-sh/uv#installation)
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```
Ensure `~/.cargo/bin` (or the directory uv is installed to) is in your `PATH`.

### Install Podman Compose (Optional)
If you plan to deploy applications using `docker-compose.yaml` files:
```bash
sudo pip3 install podman-compose
```

## Kata Installation

1.  **Download `kata.py`:**
    Obtain the `kata.py` script. You can download it directly from its source repository or copy it to your server. For example:
    ```bash
    curl -o kata.py https://raw.githubusercontent.com/piku/kata/master/kata.py # Replace with the correct URL if different
    ```

2.  **Make it Executable:**
    ```bash
    chmod +x kata.py
    ```

3.  **Place it in your PATH:**
    It's recommended to place `kata.py` in a directory that is part of your user's `PATH`, such as `~/bin` or `~/.local/bin`.
    ```bash
    mkdir -p ~/bin
    mv kata.py ~/bin/
    ```
    Ensure `~/bin` is in your `PATH`. If not, add it to your shell's configuration file (e.g., `~/.bashrc`, `~/.zshrc`):
    ```bash
    echo 'export PATH="$HOME/bin:$PATH"' >> ~/.bashrc
    source ~/.bashrc
    ```

4.  **Run Initial Setup:**
    This command will create necessary directories under `~/.kata` (or `$KATA_ROOT` if set) and check for dependencies.
    ```bash
    kata.py setup
    ```
    If you named the script `kata` instead of `kata.py` in your `~/bin` folder, then run:
    ```bash
    kata setup
    ```
    *(The rest of this guide will assume you have `kata.py` aliased or directly named `kata` in your PATH)*

5.  **Setup SSH Key for Deployments:**
    To enable deployments via `git push`, you need to add your SSH public key to Kata.
    Replace `~/.ssh/id_rsa.pub` with the path to your public key if it's different.
    ```bash
    kata setup:ssh ~/.ssh/id_rsa.pub
    ```
    If your public key is on a different machine, you can pipe it in:
    ```bash
    # ssh-copy-id -i ~/.ssh/my_key_for_kata_server.pub user@kata-server-ip # First, ensure your key is on the server
    # Then, on the kata server:
    # kata setup:ssh ~/.ssh/my_key_for_kata_server.pub
    # OR, directly from your local machine:
    cat ~/.ssh/my_key_for_kata_server.pub | ssh user@kata-server-ip "kata setup:ssh -"
    ```
    This will configure the `~/.ssh/authorized_keys` file on the server to allow Kata to handle git pushes.

## Post-Installation

### Systemd User Services
Kata uses `systemd --user` services to manage applications. Ensure your user can run lingering systemd services:
```bash
loginctl enable-linger $(whoami)
```
This allows your apps to start on boot and continue running after you log out.

### Caddy Configuration
Kata will automatically generate Caddy configurations for your applications. Caddy needs to be running as a system service.
```bash
sudo systemctl enable --now caddy
```
Ensure your firewall allows traffic on ports 80 and 443.

## Deploying Your First App
Once Kata is installed and `setup:ssh` has been run, you can deploy applications by:
1.  Ensuring your local machine has an SSH key authorized by Kata.
2.  Adding a git remote to your application's repository:
    ```bash
    git remote add kata user@kata-server-ip:my-app-name
    ```
3.  Pushing your code:
    ```bash
    git push kata main # Or your default branch
    ```
Kata will then detect the application type, build it, and deploy it.

For more details on application configuration and management, refer to the Kata documentation or use `kata help`.
