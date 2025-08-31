# Minimal Python example

A tiny FastAPI app deployed with Kata.

## Files

- `kata-compose.yaml` — stack definition with Caddy routing to localhost
- `app.py` — FastAPI app with a single endpoint
- `requirements.txt` — Python deps installed into /venv by Kata runtime

## How to try

1. Ensure Kata is set up on the host:
   - `kata setup`
2. Copy this folder to your Kata apps directory (replace APP with your app name):
   - `cp -a docs/examples/minimal-python "$HOME/app/APP"`
3. Deploy using the internal hook (or push via git if you set that up):
   - `echo "0000000000000000000000000000000000000000 $(git rev-parse HEAD) refs/heads/main" | kata git-hook APP`
   - Or simply run `kata restart APP` if the app dir already exists
4. Open [http://localhost/](http://localhost/) — Caddy will proxy to the app on 127.0.0.1:8000.

Notes:

- If you’re using a real domain, set `SERVER_NAME` accordingly in `kata-compose.yaml`.
- If your Caddy runs in Docker attached to the app network, you can change the upstream to `web:$PORT` and remove the host port mapping.
