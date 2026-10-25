# Hosting the YellowLink server on Hetzner (Ubuntu)

You need: a Hetzner Cloud server running Ubuntu 22.04/24.04 (the smallest CX/CAX type is plenty),
its public IP address, and the root password or SSH key you chose when creating it.

Replace `<server-ip>` below with your server's IP and `<owner>/<repo>` with the GitHub repo.

## 1. Log in

On your own computer (Windows: open "Terminal" or PowerShell):

```bash
ssh root@<server-ip>
```

Type `yes` the first time when asked about the fingerprint.

## 2. Install Docker

```bash
curl -fsSL https://get.docker.com | sh
docker --version          # should print a version
```

## 3. Download YellowLink

```bash
cd /opt
git clone https://github.com/<owner>/<repo>.git yellowlink
cd /opt/yellowlink/server
```

(If `git` is missing: `apt update && apt install -y git`.)

## 4. Set the room key (password)

Pick a password your friends will type into the client, then:

```bash
echo "YL_KEY=pick-a-secret-here" > .env
mkdir -p data && chown 1000:1000 data     # the server runs as an unprivileged user (uid 1000)
```

Optional: to change the welcome message, latest client version or download link, copy the example
config into the data folder and edit it (`nano data/config.json`, save with Ctrl+O, exit with Ctrl+X):

```bash
cp config.example.json data/config.json && chown 1000:1000 data/config.json
```

The `YL_KEY` in `.env` always wins over the `key` in `config.json`.

## 5. Start it

```bash
docker compose up -d
docker compose logs -f     # you should see "listening on 0.0.0.0:8765"; Ctrl+C to stop watching
```

It starts automatically after reboots (`restart: unless-stopped`).

## 6. Open port 8765

**Hetzner Cloud firewall** (only if your server has one attached): Hetzner Cloud Console ->
Firewalls -> your firewall -> Inbound rules -> Add rule: Protocol `TCP`, Port `8765`,
Source `Any IPv4` + `Any IPv6` -> Save.

**ufw** (Ubuntu's own firewall), only if it is active:

```bash
ufw status                 # if it says "Status: active":
ufw allow 8765/tcp
```

## 7. Connect

Players enter in the client:

- Server: `ws://<server-ip>:8765`
- Room key: the password from step 4

## Everyday commands

Run these from `/opt/yellowlink/server`:

| What | Command |
|---|---|
| Update to the newest version | `git pull && docker compose up -d --build` |
| Watch the logs | `docker compose logs -f` (last hour: `docker compose logs --since 1h`) |
| Restart | `docker compose restart` |
| Stop | `docker compose down` |
| Change the key | edit `.env`, then `docker compose up -d` |
| Back up player data | copy `data/players.json` somewhere safe |

## Optional: secure `wss://` with a domain (Caddy)

Do this if you own a domain. Caddy gets a free HTTPS certificate automatically.

1. At your DNS provider, create an `A` record, e.g. `yellowlink.example.com` -> `<server-ip>`.
2. Open ports 80 and 443 (Hetzner firewall rule like step 6, and `ufw allow 80,443/tcp` if ufw is active).
3. Install Caddy:

   ```bash
   apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
   curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
   curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | tee /etc/apt/sources.list.d/caddy-stable.list
   apt update && apt install -y caddy
   ```

4. Replace the contents of `/etc/caddy/Caddyfile` (`nano /etc/caddy/Caddyfile`) with:

   ```
   yellowlink.example.com {
       reverse_proxy 127.0.0.1:8765
   }
   ```

   Then `systemctl reload caddy`.

5. Players now use `wss://yellowlink.example.com` as the server address.
6. Optional hardening: so the server is reachable only through Caddy, change the port line in
   `docker-compose.yml` to `- "127.0.0.1:8765:8765"`, run `docker compose up -d`, and remove the
   8765 firewall rules.

## Alternative: without Docker (systemd)

Clone the repo somewhere else (e.g. `git clone ... ~/yellowlink-src`), `apt install -y python3-venv`, then
follow the install steps at the top of `server/yellowlink.service` from inside `~/yellowlink-src/server`
(it installs into `/opt/yellowlink` with a Python venv; logs via `journalctl -u yellowlink -f`).
