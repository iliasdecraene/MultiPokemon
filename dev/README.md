# Dev harnesses

End-to-end scripts that drive real PyBoy instances (and a local server) headlessly. They need the ROM and a few
save states, which are **not in git** (`dev/states/` is ignored). Put these in `dev/states/`, or point the
`YL_ROM` / `YL_STATES` environment variables elsewhere:

| file | how to make it |
|---|---|
| `yellow.gb` | your Pokémon Yellow (USA/Europe) ROM, SHA1 `cc7d03262ebfaf2f06772c1a480c7d9d5f4a38e1` |
| `room.state` | `python dev/e2e/make_room_state.py` (auto-plays the intro; player ends in the bedroom, map 0x26) |
| `yellow.sav` | `python dev/e2e/make_save.py` (saves in-game from `room.state`; used by `boottest.py`) |
| `pallet_after_lab.state` | made with `dev/worldview/progress.py` (player in Pallet Town after Oak's lab) |

Run with `SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy` on a headless machine (the scripts set it themselves).

| script | checks |
|---|---|
| `e2e/e2e.py` | server + 2 clients: see each other walk, trade (incl. Kadabra→Alakazam), practice battle |
| `e2e/pvp_e2e.py` | full live PvP battle through the server (request → accept → battle with a switch → result) |
| `e2e/menutest.py` | friends stay visible while either player has a menu open, drawn behind menus |
| `e2e/boottest.py` | instant continue from a save + volume slider |
| `e2e/widetest.py` | wide view in Pallet Town with a friend in the margin |
| `e2e/bordertest.py` | friend on a connected map (Route 1) drawn across the border from Pallet Town |
| `e2e/walkdiag.py` | wide view while walking: counts frames with black margins (must be 0) |
| `e2e/libtest.py` | auto-resume across restarts + mouse-driven Multiplayer/Regions panels (click → battle request) |
| `e2e/traveltest.py` | travelling to another game and back (separate saves/snapshots, Yellow-only features off elsewhere) |
| `e2e/chattest.py` | chat box: click to type, Send, scrolling, click-away unfocus, wide-view toggle |
| `e2e/diagtest.py` | logging, error tolerance, bug reports (live + report-only connection), crash marker |
| `e2e/pvp_proto.py` | two emulators linked in-process (no server) — the original link-battle sync proof |
| `worldview/verify.py` | wide-view renderer vs real screen, pixel agreement report |

Expected output: see the `print` lines; screenshots go to a temp dir printed at the end.
