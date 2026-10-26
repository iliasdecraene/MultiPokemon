# Definitive Platinum support — shared brief (0.7.7)

Worktree: `/home/claude/yl-defplat` (branch `definitive-platinum`, from main 0.7.6). Read `CLAUDE.md`, `MEMORY.md`,
`SPEC.md` first. `dev/states/` is a symlink to the main checkout's ROMs/states (gitignored — never commit ROMs, patches,
saves or states; never copy them into tracked paths). Agents don't `git commit`/push; the coordinator commits.
Notes go in `dev/defplat/STATUS.md` (short, dated).

## What it is
"Pokémon Definitive Platinum" by BigNation (final, 2024; PokéCommunity thread
https://www.pokecommunity.com/threads/pok%C3%A9mon-definitive-platinum-complete.539178/): all 493 obtainable, the
Darkrai/Shaymin/Arceus/Rotom-room Mystery Gift events unlocked, new events for legendaries/mythicals, trade
evolutions at level 36 (item evos: level up holding the item), harder trainers, NO changes to stats/types/moves/
learnsets. Its ReadMe says vanilla saves stay compatible.

## Files we have (dev/states/, gitignored)
- `platinum.nds` — the supported vanilla Platinum (US rev 0, SHA1 ce81046eda7d232513069519cb2085349896dec7).
- `defplat_rev0.xdelta` (20.4 MB) — made from "Pokemon - Platinum Version (USA).nds" = our rev 0. Applies cleanly.
- `defplat_rev1.xdelta` (20.4 MB) — made from "(USA) (Rev 1)". We have no rev 1 ROM; its 13 windows' target sizes and
  Adler-32s are identical to the rev 0 patch's, so both produce the SAME output.
- `defplat.nds` — rev 0 + patch = Definitive Platinum, 105,001,364 bytes, SHA1
  **2f368f0f68f98058699a8dd0b1f9af3c970172a2**, header game code `CPUE`. Boots in py-desmume (title + BigNation intro).
- Patches are xdelta3/VCDIFF with LZMA secondary compression (id 2): one xz stream per section kind (data/inst/addr)
  spanning all windows (LZMA_SYNC_FLUSH per window), Adler-32 per window. A working pure-Python decoder draft is
  `dev/vcdiff_draft.py` (decodes in ~6 s here). The pip `xdelta3` package can't read these (no LZMA).

## Goal
1. Definitive Platinum (that SHA1) is a fully supported Sinnoh game in YellowLink — everything that works with vanilla
   Platinum works with it: playing, resume, saves, PC database + trades/gifts (also with vanilla Platinum, Yellow and
   Crystal players), friends walking in Sinnoh, Exp Share All, badges (champion, Sinnoh dex 209, shinies), Soul Link
   (areas, referee, trade evolutions — DefPlat has no trade evolutions, so the picker is just empty/explains it),
   Pokédex marking. Vanilla Platinum keeps working exactly as before.
2. Players don't have to patch anything themselves: YellowLink patches the player's OWN vanilla Platinum (rev 0 or rev 1)
   with a patch file it gets from the player's server (Ilias's private server; the patch is BigNation's work — we never
   put it in the repo/exe; the README credits BigNation + links the thread). The original ROM file is never modified;
   the result must have the exact SHA1 above or it's discarded.
3. Server option for the Sinnoh game: `YL_SINNOH` = `any` (default: vanilla or Definitive, players choose) |
   `definitive` (the server asks everyone to switch; Soul Link Platinum runs need both players on the same Sinnoh game).
   Ilias decides later which to use; build both.

## Split
- **Game agent `defplat-game`** (owns: platinum.py, platinum_exp.py, platinum_pc.py, sinnoh_view.py, nds_core.py,
  ndsrom.py, pk4.py, platinum_areas.py + generator, the Platinum parts of challenge_view.py/tradeevo.py/badges adapters,
  dev/platinum/*, dev/defplat/verify*.py, new dev states `dev/states/dp_*`): find every difference that matters between
  vanilla and Definitive (overlay/code addresses used by hooks: Exp Share overlay 16, PC box UI overlay 19, any other
  exec hooks or code-signature checks; data tables we read from the ROM: personal, evolutions, encounter/maps, dex
  lists; RAM pointers in MEMORY_MAP.md), verify everything on the hack, make the adapter work for both SHA1s (a small
  variant table, never guessing), make dev states for the hack (e.g. load a vanilla battery save into the hack — check
  that's really compatible — and new game), and run the whole Platinum suite against it (dev/platinum/verify.py,
  soullink_verify.py, expshare_pt.py, pt_pctest.py, pt_trade_e2e.py, pt_friends.py, platinum_boot.py,
  soullink_platinum.py, challenge_flow_platinum.py, soullink_tradeevo.py, badges verify) — parameterise the scripts by
  ROM (env `YL_PT_ROM=defplat`) instead of copying them.
- **App agent `defplat-app`** (owns: client/yellowlink/vcdiff.py (from the draft, with tests and a progress callback),
  library.py (identify vanilla rev 0/rev 1/Definitive; `App.full` for the new SHA1), app.py/panels.py/ui.py patch flow,
  config.py, net.py, updater.py if useful, server/* (patch storage in the server's data folder, e.g.
  `data/patches/definitive_platinum/{rev0,rev1}.xdelta`, served to clients in chunks over the websocket with SHA1;
  `YL_SINNOH`; DEPLOY.md: how Ilias puts the two patch files on his servers with a copy-pasteable command), SPEC.md
  protocol, README, tests, `dev/e2e/defplat_patch_flow.py`): the patching flow end to end — server offers the patch,
  Regions shows "Your server plays Definitive Platinum — make it from your Platinum? [Make it]" (or optional), progress
  bar, result verified by SHA1, stored next to the player's other game files (YellowLink's ROM library), the Sinnoh
  region then runs it; backup of the player's Sinnoh save before the first run on the hack; vanilla rev 1 players
  (unsupported today) can still patch (detect rev 1 by header: game code CPUE + revision byte 0x1E = 1, and let the
  patch's Adler-32 checks + the final SHA1 prove it). Soul Link: a Platinum run records which Sinnoh game it uses and
  both players must have it.
- Coordinate interface changes in STATUS.md (e.g. the variant constants the game agent exposes: SHA1s, names).
