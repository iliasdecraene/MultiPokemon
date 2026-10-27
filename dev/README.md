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
| `crystal.gbc` | your Pokémon Crystal (USA v1.0) ROM, SHA1 `f4cd194bdee0d04ca4eac29e09b8e4e9d818c133` |
| `crystal_room.state`, `crystal_party.state`, `crystal.sav`, `crystal.rtc` | `python dev/crystal/make_states.py` (plays the intro, gets Cyndaquil, adds Pidgey + Sentret, saves in-game) |

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
| `e2e/regionstest.py` | Regions panel by region (one game per region; version chooser when several are added) |
| `e2e/chattest.py` | chat box: click to type, Send, scrolling, click-away unfocus, wide-view toggle |
| `e2e/diagtest.py` | logging, error tolerance, bug reports (live + report-only connection), crash marker |
| `e2e/pvp_proto.py` | two emulators linked in-process (no server) — the original link-battle sync proof |
| `worldview/verify.py` | wide-view renderer vs real screen, pixel agreement report |
| `crystal/verify.py` | Crystal memory map + `crystal.py`: walking px/py vs camera, sprite pixel match, party/box writes accepted by the game through SAVE/CONTINUE/CHANGE BOX, Bill's PC hooks, RTC |

Expected output: see the `print` lines; screenshots go to a temp dir printed at the end.
- `pc_actions.py` — scripted Bill's PC button presses (deposit/withdraw/release/change box) from `pokecenter_pc.state`.
- `reload_save.py` — "Back to my last in-game save" (SPEC.md section 5) on the real Yellow ROM: in-game save, refusal
  after a deposit / a gift away, manual soft reset (party clone taken out, PC intact), the button's restart from
  the save (position, party, PC store); screenshots of the button + confirmation at GB 3x and DS 1.5x.
  `reload_save_pt.py`: Platinum's own soft reset (L+R+Start+Select) is noticed and the PC is lined up after CONTINUE.
- `pctest.py` — PC database e2e on the real ROM (deposit, commit to server, stand-ins, restart).
- `crossgame.py` — Yellow and Crystal share one PC database (real PC menus in both games).
- `crystal_mp.py` — two Crystal players + one Yellow player: seeing each other, Crystal trade with evolution, cross-game refusal.
- `shiny_alert.py` — shiny alerts (SPEC.md section 11): a Yellow client and a Crystal client (later Platinum) on a local
  server; shinies planted in all three games -> the other player gets the chat line, banner and (counted) jingle; the
  Yellow caught ball at 2x/3x/4x; the server's rate limit. Screenshots (GB 3x/4x, DS 1.5x) in the printed temp folder.
- `../shiny/verify.py [yellow|crystal|platinum]` — the shiny alerts' game side on the real ROMs: the once-per-battle
  watch on real encounters, planted shinies (Yellow DVs, Crystal DVs, Platinum PID incl. the second mon of a double
  battle), Yellow's enemy-HUD check / caught ball tile, trainer battles never alert, our mixer next to DeSmuME.
- `badges_flow.py` — badges (SPEC.md section 9): main-game badges by RAM edits seen by a friend, gold/silver Soul Link badges from runs, the Yellow "Shiny!" tag; screenshots of the Regions/Multiplayer panels (3x, 4x, DS 1.5x layout).
- `challenge_flow.py` — challenges with two clients: invite, fresh separate save, pause/partner-left pop-up, continue, closing the game, abandon.
- `soullink_yellow.py` / `soullink_crystal.py` — Soul Link referee in the real games with two clients (catches, links, faints, PC rule, blackout).
- `soullink_tradeevo.py [yellow|crystal|platinum]` — trade evolutions with the Soul Link partner (SPEC.md section 7) on all three ROMs: Kadabra -> Alakazam, Onix + Metal Coat -> Steelix, Everstone blocks; the evolved mon stays linked and a later faint kills both.
- `soullink_mem_yellow.py` and `../crystal/soullink_verify.py` — verify the game-memory helpers the referee uses.
- `pctrade_e2e.py` — PC trades + gifts between a Yellow and a Crystal player (real PC menus, server swap, no re-import).
- `expshare_gb.py` — Exp Share All on the real Yellow and Crystal ROMs: wild / two participants / trainer x1.5 / level-up,
  move learning + forget prompt, evolution / EXP.ALL + held Exp. Share, hooks off == no hooks (whole WRAM).
- `expshare_forced.py [--off|--none] <script>` — runs another e2e script with Exp Share All forced on (or off / not
  installed) and checks link battles never get the rule.

## Complete Sinnoh (SPEC.md section 11)
- Every Platinum harness takes `YL_PT_ROM=vanilla` (default) or `YL_PT_ROM=sinnoh` (the Complete Sinnoh build made
  from `dev/states/platinum.nds` by `dev/sinnoh/build_rom.py`; `YL_SINNOH_SPEC=test` = the tiny test spec). Run the
  whole Platinum list above with both. Fake Soul Link partners advertise the same Sinnoh version (`ptrom.sinnoh_caps()`).
  Each run copies the 128 MB ROM into its temporary app folder and builds another 128 MB file there: `ptrom` deletes
  those `*.nds` / `*.part` copies at exit (screenshots and logs stay; `YL_KEEP_ROM_COPIES=1` keeps them). Without
  that, a day of runs filled the disk and the build failed with "not enough free disk space".
- `dev/sinnoh/build_rom.py [--update]` — build the dev ROM, print every byte range that differs from vanilla;
  `--update` stores the new SHA1 in `sinnoh_rom.EXPECTED` after a spec change.
- `dev/sinnoh/verify.py [rom] [compat] [wild] [events] [regis] [swarm] [area]` (default: all, ~1.5 h) — the built
  ROM's bytes, vanilla snapshots in it (cached FAT refresh), forced wild encounters on edited maps, the four Mystery
  Gift events played to the legendary's battle, the Regi ruins -> Regigigas chain in one save (real catches), the
  2-hour swarm switch, the Pokédex area screen (vs. vanilla in a second process). `YL_SINNOH_SHOTS=<dir>` saves a
  screenshot for every failed check. Uses `~/.config/desmume/platinum-sinnoh.dsv`: never run two of these at once
  (the app harnesses use their own folders and can run alongside).
- `dev/e2e/sinnoh_app.py` — the app: builds/reuses/repairs `roms/platinum-sinnoh.nds` (settings keep the player's
  own file), resumes vanilla snapshots (old tag kept until the refresh), events by condition, swarm toast (not
  repeated after a restart), plain-Platinum fallback.
- `dev/e2e/sinnoh_pairing.py` — a real Yellow client and a real Complete Sinnoh client: both advertise the same
  `sinnoh_v<spec>`, a failed build shows as `sinnoh_failed` with the right tip, and a Platinum Soul Link starts.

Raids: the e2e harnesses start their servers with `YL_RAIDS=off` - a scheduled raid (they're on by default and the
server ships real spot data) would put a den / edge arrow / raid card on screen and break pixel checks such as
pt_friends' "friend hidden" test. `dev/e2e/raid_flow.py` is the raid test (it runs its own server in-process).
