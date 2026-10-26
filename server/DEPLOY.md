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


## Updating to a new version

```bash
cd /opt/yellowlink && git pull
cd server && docker compose up -d --build
```

Players on an older client then see an "update available" notice in the game.

## Reading bug reports from players

When a player presses F3 (or after a crash) and clicks **Send to host**, the report is saved on the server:

```bash
ls -lt /opt/yellowlink/server/data/reports | head      # newest first
cat /opt/yellowlink/server/data/reports/<file>.txt
```


## A second group of friends (another server on the same machine)

Each group gets its own server: its own port, room password, players, PC databases and challenges. The groups don't
see each other. Example for a second group on port 8766:

```bash
sudo git clone https://github.com/iliasdecraene/MultiPokemon.git /opt/yellowlink2
cd /opt/yellowlink2/server
sudo tee .env > /dev/null <<'ENV'
YL_KEY=pick-a-different-password
YL_SERVER_NAME=Second Group
YL_NAME=yellowlink2
YL_HOST_PORT=8766
COMPOSE_PROJECT_NAME=yellowlink2
ENV
sudo mkdir -p data && sudo chown 1000:1000 data     # the server runs as uid 1000 and must own its data folder
sudo docker compose up -d --build
```
Players of that group use `ws://<server-ip>:8766` and that password. Open port 8766 if a firewall is on.
Someone in both groups adds the second one in the game (Multiplayer → Switch server → Add a server...) and switches
between them there; each server keeps its own saves, PC and challenges on their computer.
If `docker ps` shows it "Restarting" and `docker logs <name>` says `Permission denied: '/data/...'`, the data folder
has the wrong owner: `sudo chown -R 1000:1000 data` in its `server` folder, then `sudo docker compose up -d`.
Update it like the first one: `cd /opt/yellowlink2 && git pull && cd server && docker compose up -d --build`.

### Name and welcome message
`YL_SERVER_NAME` (shown in every player's sidebar and when they connect) and `YL_MOTD` (welcome line in the chat) can be
set in a server's `.env`. Apply a change with `docker compose up -d` in that server's `server` folder.

## Merging two servers into one

Everything a player has (saves, PC, Soul Link runs) stays: the second server's data is copied into the first, and
the second server becomes a *forwarder* that tells every game (0.7.1 or newer) the new address and password - the
game switches over by itself, keeping its save folder. Games older than 0.7.1 get the "press U to update" notice
there first. Example: the second server in `/opt/yellowlink2` goes into the first one in `/opt/yellowlink`.

1. Publish the release with the merge support (0.7.1 or newer) and update both servers (`sudo yl-update`, or the
   update command in each folder).
2. Stop both, merge, fix the file owner, start the first one again:
   ```bash
   cd /opt/yellowlink2/server && sudo docker compose stop
   cd /opt/yellowlink/server && sudo docker compose stop
   sudo python3 /opt/yellowlink/server/yellowlink_server/merge.py /opt/yellowlink2/server/data /opt/yellowlink/server/data
   sudo chown -R 1000:1000 /opt/yellowlink/server/data
   cd /opt/yellowlink/server && sudo docker compose up -d
   ```
   The merge prints what it copied and where it put a backup of the old data
   (`/opt/yellowlink/server/data.before-merge-<time>`). If it says a trade is still being finished: start the
   second server once (`sudo docker compose up -d` in its folder), stop it again and repeat.
3. Turn the second server into a forwarder (this copies the first server's password into its settings, nothing to
   type) and start it:
   ```bash
   echo "YL_MOVED_TO=ws://91.107.193.182:8765" | sudo tee -a /opt/yellowlink2/server/.env
   sudo grep '^YL_KEY=' /opt/yellowlink/server/.env | sed 's/^YL_KEY=/YL_MOVED_KEY=/' | sudo tee -a /opt/yellowlink2/server/.env > /dev/null
   cd /opt/yellowlink2/server && sudo docker compose up -d
   ```
4. Leave the forwarder running for a few weeks so everyone gets moved (it uses almost nothing). Then
   `cd /opt/yellowlink2/server && sudo docker compose down` removes it.

Someone who was on both servers gets their second save as an extra entry in the server list ("<name> (save)"), now
pointing to the merged server - two characters with the same name. Remove that entry in the game (Multiplayer →
Servers → Remove) if you don't want it; its saves stay in the YellowLink folder.
