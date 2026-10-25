# YellowLink

Private multiplayer for Pokémon Yellow, for you and your friends.

Everyone plays their **own** game with their own save and story. On top of that:

- **See each other.** Friends on the same map walk around in your world, with name tags.
- **Trade.** Open the link menu, pick a friend, and both choose a Pokémon and confirm. Trade evolutions work (Kadabra → Alakazam and the others).
- **Battle each other, live**: challenge a friend from anywhere in the world (Tab → B). They accept, both games jump into a real link battle and each of you picks your own moves. Exhibition match: teams are healed and you're put back where you were afterwards.
- **Practice**: fight a copy of a friend's team played by the game's AI (Tab → P), even when they're offline.
- **Wide view** (press **V**): see twice as much of the world around you, NPCs and friends included. The real Game Boy picture stays untouched in the middle.
- **Straight into your game**: with a save, the intro and title screen are skipped and Continue is picked for you.
- **Chat.** You can also see who's online, and the app shows a notice when an update is available.

> **No ROM is included, and none ever should be.** Each player needs their own copy of
> *Pokémon Yellow Version (USA, Europe)*. The repo contains only original code; all graphics come from
> your own game while it runs.

---

## For players

1. Download **YellowLink.exe** from the [Releases page](../../releases/latest). Put it in its own folder (e.g. `Documents\YellowLink`), not in Program Files.
2. Run it. The first time, a setup window asks for:
   - **Your name.** Friends see this above your character.
   - **Server address**, e.g. `ws://123.45.67.89:8765`. Ask whoever hosts the server.
   - **Room password.** Same source.
   - **Your Pokémon Yellow ROM** (`.gb` file).
   - *(optional)* **An existing save** (`.sav`) from another emulator, to continue your adventure.
3. Play.

| Key | What it does |
|---|---|
| Arrow keys | D-pad |
| **X** or **A** | A button (talk, confirm, next text) |
| **Z** or **S** | B button (back) |
| Enter / Backspace | Start / Select |
| **Tab** | Link menu: pick a trainer, then **B** battle (live), **R** trade, **P** practice vs their team |
| F4 | Leave a live battle |
| **T** | Chat |
| Space (hold) | Fast-forward |
| M | Mute |
| − / + | Volume (or drag the slider in the sidebar) |
| **V** | Wide view on/off |
| F5 / F6 | Smaller / bigger window |
| F1 | Help |
| **F3** | Report a problem (shows the log with Copy / Send to host) |
| F2 | Settings (opens after you close the game) |

Gamepads work too.

**Saves.** Your progress is saved when you save in-game (Start → Save), just like on a real Game Boy.
The save file lives in `%APPDATA%\YellowLink\saves\yellow.sav`. A backup is made every time you start the app,
in the `backups` folder next to it, and the last 15 are kept. It's a standard 32 KB save, so it also works in other emulators.

**After a trade, save in-game.**

**Updates:** when a new version is out, the sidebar says so. Press **U**: it downloads in the background while you play, then asks to restart. Save in-game before restarting.

**Something broke?** Press **F3**, or use the window that pops up after a crash, and click **Send to host**.
The report lands on the server in `server/data/reports/`. Logs are also kept in `%APPDATA%\YellowLink\logs`.

Windows SmartScreen may warn about the exe because it isn't code-signed. Click *More info → Run anyway*.

## For the host

The server is a small Python program and runs comfortably on the smallest Hetzner VPS.
Follow **[server/DEPLOY.md](server/DEPLOY.md)**. In short: install Docker, clone this repo,
set a room password in `.env`, run `docker compose up -d`, and open port 8765 in the firewall.

## For developers

```
client/   PyBoy emulator + pygame window + websocket client  (python -m yellowlink)
server/   asyncio websocket relay                            (python -m yellowlink_server)
SPEC.md   protocol + verified game memory map (the source of truth)
```

Run from source (Python 3.11+):

```bash
pip install -r client/requirements.txt
cd client && python -m yellowlink            # add --setup to reopen settings
```

Tests: `pytest server/tests client/tests`. Releases: see [RELEASING.md](RELEASING.md). Pushing a `v*` tag
builds the Windows exe with GitHub Actions and attaches it to a release.

### How it works

The client runs the game in [PyBoy](https://github.com/Baekalfen/PyBoy) and reads and writes game memory using the
addresses in `SPEC.md`, which are taken from the pret/pokeyellow disassembly and verified against the ROM:

- **Friends:** your map ID and pixel position are sent about 15 times a second. Your sprite's tiles are read from video memory, so bikes and surfing show up correctly. Friends are drawn over the game screen using your own map palette.
- **Trades:** the server runs a two-step handshake. Each client writes the received 44-byte Pokémon record into its party, and undoes the write if the other side fails.
- **Live battles:** both games run the game's own *link battle* engine (normally used with a link cable), started from the overworld with the same random-number list on both sides. Each turn's choice travels over the server instead of a cable, so both screens show the exact same battle. A save state taken before the battle is restored afterwards.
- **Practice battles:** a normal trainer battle with the friend's team swapped in and the game's AI in control.

### Roadmap

- Showing friends' following Pikachu
- Dedicated trade room / meeting spots
