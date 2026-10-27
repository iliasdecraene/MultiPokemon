# YellowLink

Private multiplayer for Pokémon Yellow (Kanto), Pokémon Crystal (Johto) and Pokémon Platinum (Sinnoh), for you and your friends.

Everyone plays their **own** game with their own save and story. On top of that:

- **See each other.** Friends on the same map walk around in your world, with name tags.
- **Trade and gift, across games.** Multiplayer → click a friend → **Trade** or **Gift**. You pick Pokémon from your PC (not your party) - one or several, or a whole box at once - so a Yellow player can trade with a Crystal player. The server swaps both PCs in one step, so a dropped connection can't duplicate or lose anything. Trade evolutions (Kadabra → Alakazam, and in Crystal Onix + Metal Coat → Steelix and friends) happen on arrival. A Pokémon your game can't hold arrives as a `?NAME` placeholder and stays safe in your PC.
- **Battle each other, live**: challenge a friend from anywhere in the world (Tab → B). They accept, both games jump into a real link battle and each of you picks your own moves. Exhibition match: teams are healed and you're put back where you were afterwards.
- **Practice**: fight a copy of a friend's team played by the game's AI (Tab → P), even when they're offline.
- **Wide view** (press **V**): see twice as much of the world around you, NPCs and friends included. The real Game Boy picture stays untouched in the middle.
- **Straight into your game**: with a save, the intro and title screen are skipped and Continue is picked for you.
- **Two regions**: Kanto is Pokémon Yellow, Johto is Pokémon Crystal. Add your Crystal game file in **Regions** and travel between them from the sidebar. Each game keeps its own progress. In Crystal you see friends and trade (trade evolutions included, Metal Coat and friends too); live battles, practice and wide view are Yellow-only for now.
- **Sinnoh (Pokémon Platinum, Nintendo DS)**: add your Platinum game file in **Regions** and travel there like any region. Both DS screens are shown (top above bottom) and the **mouse is the touch screen** (click/drag on the lower screen). You see your Platinum friends walking around in Sinnoh, and trades/gifts work with Yellow and Crystal players through the shared PC. Soul Link works in Platinum too; battles, practice and wide view aren't available there (yet).
- **One PC for all games**: your PC boxes live in your own database (also backed up on the server). Deposit a Pokémon in Yellow and it's in the PC in Crystal and Platinum, and the other way round. In Platinum your YellowLink PC fills boxes 1-14, 20 per box; anything you put elsewhere is tidied into free spots. Pokémon that can't exist in a game (a Chikorita in Yellow) show up as `?CHIKORITA` and stay safely in the PC.
- **No saving needed**: closing YellowLink remembers exactly where you were (it also snapshots every 30 seconds), and every game resumes where you left it.
- **Challenges: Soul Link.** In Multiplayer, click a friend → **Challenges** → *Start a Soul Link*. You both start a
  fresh save (separate from your main games) of Yellow, Crystal or Platinum, and YellowLink referees the Nuzlocke for
  you: only the first encounter per area counts, catches in the same area are linked partners, a fainted Pokémon (and
  its partner) goes to the PC for good, boxing one boxes its partner, and a blackout ends the run. The run only plays
  while you're both in it; pause any time and **Continue** together later. Finish it (both reach the Hall of Fame) to
  earn the Soul Link badge (gold without Exp Share All, silver with it). A Platinum Soul Link needs both of you on the latest YellowLink (press **U** to update).
  **Trade evolutions** are always allowed in a run: open **Soul Link** → *Trade evolution*, pick a Pokémon that
  evolves by trade (Kadabra, Machoke, Graveler, Haunter - in Crystal and Platinum also the ones holding their item,
  like Onix with a Metal Coat; an Everstone stops it) and your partner gets a "help evolve it?" question. It goes over
  and comes straight back evolved: still yours, still linked to its partner.
- **Exp Share All** (Settings, top right of the sidebar - on by default): like the newer games, Pokémon that battle get
  full experience and the rest of your party gets half, in Yellow, Crystal and Platinum. Levels, new moves and
  evolutions happen the normal way. Turn it off for the original rules. In a Soul Link the run decides: whoever
  starts it picks Exp Share All on or off for both players.
- **Complete Sinnoh** (Platinum): every Pokémon family can be found in Sinnoh. YellowLink makes its own copy of your
  Platinum (your file is never changed) where only the wild Pokémon lists are extended - normal story, trainers and
  difficulty. All 12 starters appear in the wild before the League (5 %), Murkrow/Misdreavus/Glameow/Stunky and the
  Pokémon that needed a GBA cartridge have normal spots, and the Pokédex Area map shows the new places. The Mystery
  Gift events are unlocked (Member Card - Darkrai, Oak's Letter - Shaymin, Azure Flute - Arceus, Secret Key - Rotom's
  room), the Regi ruins open without an event Regigigas, and swarms change every 2 hours for everyone.
- **Badges.** Every region has badges you earn in your main game: **Champion** (enter the Hall of Fame),
  **Pokédex** (catch the whole regional dex: Kanto 150 without Mew, Johto 249 without Mew and Celebi, Sinnoh 209
  without Manaphy - Pokémon you get through YellowLink trades or from your other games count, like real trades),
  **Shiny hunter** (catch shinies yourself: 1, 10, 25, 50, 100) and the **Soul Link** badge. See yours under each
  region in **Regions**, and a friend's by clicking them in **Multiplayer**. Yellow has no shinies, so a Yellow
  Pokémon that would be shiny in Crystal counts as shiny in YellowLink - a **Shiny!** tag shows in wild battles.
- **Shiny alerts.** When a shiny shows up in someone's wild battle (any game, also inside a Soul Link), everyone
  online gets a chat line, a big sparkly banner at the top of the game and a little jingle (it plays even when your
  game sound is turned down). In Yellow a small Poké Ball next to a wild Pokémon's name means you've caught that
  species already, like in Crystal.
- **Chat.** You can also see who's online, and the app shows a notice when an update is available.

> **No ROM is included, and none ever should be.** Each player needs their own copy of
> *Pokémon Yellow Version (USA, Europe)*, plus *Pokémon Crystal Version (USA, Europe)* (v1.0 for multiplayer) for Johto and *Pokémon Platinum Version (USA)* (`.nds`) for Sinnoh. The repo contains only original code; all graphics come from
> your own game while it runs.

---

## For players

1. Download **YellowLink.exe** from the [Releases page](../../releases/latest). Put it in its own folder (e.g. `Documents\YellowLink`), not in Program Files.
2. Run it. The first time, a setup window asks for:
   - **Your name.** Friends see this above your character.
   - **Server address**, e.g. `ws://123.45.67.89:8765`. Ask whoever hosts the server.
   - **Room password.** Same source.
   - **Your Pokémon game file**: Yellow (`.gb`), Crystal (`.gbc`) or Platinum (`.nds`). Add the others later in **Regions**.
   - *(optional)* **An existing save** (`.sav`) from another emulator, to continue your adventure.
3. Play.

| Key | What it does |
|---|---|
| Arrow keys | D-pad |
| **X** or **A** | A button (talk, confirm, next text) |
| **Z** or **S** | B button (back) |
| Enter / Backspace | Start / Select |
| **Tab** | Multiplayer panel (or click **Multiplayer** in the sidebar): click a trainer, then Battle, Trade or Practice |
| F4 | Leave a live battle |
| **T** | Chat |
| Space | Fast-forward on/off |
| M | Mute |
| − / + | Volume (or drag the slider in the sidebar) |
| **V** | Wide view on/off |
| F5 / F6 | Smaller / bigger window |
| **D / C** | Platinum: X / Y buttons |
| **Q / W** | Platinum: L / R buttons |
| Mouse on the lower screen | Platinum: touch screen |
| F1 | Help |
| **F3** | Report a problem (shows the log with Copy / Send to host) |
| F2 | Settings (opens after you close the game) |
| F7 | Back to your last in-game save (also in **Settings**) |

Gamepads work too.

**Saves.** Your progress is saved when you save in-game (Start → Save), just like on a real Game Boy.
The save file lives in `%APPDATA%\YellowLink\saves\yellow.sav`. A backup is made every time you start the app,
in the `backups` folder next to it, and the last 15 are kept. It's a standard 32 KB save, so it also works in other emulators.

**Back to your last in-game save** (going for a shiny or a legendary? save in front of it): **Settings →
Back to my last in-game save** (or **F7**) restarts the game from your last Start > Save, straight into Continue.
Everything since that save is lost, but your YellowLink PC and trades stay as they are. If you put a Pokémon in
the PC, took one out, or traded/gave one away since your last save, it says so and asks you to save in-game first
(otherwise a Pokémon could exist twice). Not available inside a Soul Link. The game's own soft reset works too:
**A + B + Start + Select** in Yellow/Crystal (keys X + Z + Enter + Backspace), **L + R + Start + Select** in
Platinum (Q + W + Enter + Backspace) - a Pokémon you put in the PC after your last save then stays in the PC and
leaves your party, so it's never doubled. (Where you were before going back is kept in the `backups` folder.)

**More than one group of friends?** Open **Multiplayer → Switch server → Add a server...** and enter the other
group's address and password. Each server keeps its own saves, PC and challenges (saves of extra servers live in
`%APPDATA%\YellowLink\servers\`), so a different group means a different adventure. Switch back any time from the
same list; your game is saved first and continues where you left off. Want the **same game on two servers**
(two groups of friends, one adventure)? In the server's window pick *Save: Same save as …* - both servers then
use one save and one PC, and each group still sees you as their own player.

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
- **Trades:** between PC databases. The server swaps the entries in both players' stored PCs atomically and sends each a delta, which the client applies to its local store (see SPEC.md).
- **Live battles:** both games run the game's own *link battle* engine (normally used with a link cable), started from the overworld with the same random-number list on both sides. Each turn's choice travels over the server instead of a cable, so both screens show the exact same battle. A save state taken before the battle is restored afterwards.
- **Practice battles:** a normal trainer battle with the friend's team swapped in and the game's AI in control.
- **Platinum (DS):** runs in [DeSmuME](https://github.com/TASEmulators/desmume) through
  [py-desmume](https://github.com/SkyTemple/py-desmume) (both GPL; the Windows exe bundles them, their source is at
  those links). Addresses come from the pret/pokeplatinum decompilation, verified on the ROM
  (`dev/platinum/MEMORY_MAP.md`). Friends are projected into the 3D overworld with the game's own camera and drawn
  with Lucas/Dawn sprites decoded from your own ROM.

### Roadmap

- Showing friends' following Pikachu
- Dedicated trade room / meeting spots
