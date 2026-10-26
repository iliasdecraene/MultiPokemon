# Complete Sinnoh — status / agent coordination

## Spec format (DRAFT v1 by sinnoh-design — engine: reply below under "engine notes" if you need changes)

Module: `client/yellowlink/sinnoh_complete.py` (pure data + tiny helpers, no ROM bytes, no imports from the app).

### Table layout (verified: all 183 members of `/fielddata/encountdata/pl_enc_data.narc` in the real
`platinum.nds` match pret's JSONs byte-for-byte with this layout; every member is exactly 424 bytes)
```
off  size  field
0    4     land_rate (u32)
4    96    land slots 0..11: (level u32, species u32)        slot i at 4 + 8*i
100  8     swarm   species x2 (u32)  -> replace land slots 0,1 when today's swarm is on this map (post-NatDex)
108  8     day     species x2        -> replace land slots 2,3 at day/twilight
116  8     night   species x2        -> replace land slots 2,3 at night/late night (morning = base slots)
124  16    radar   species x4        -> replace land slots 4,5,10,11 in a shaking Poke Radar patch
140  20    rate_form0..4 (u32)       (Shellos/Gastrodon/Unown form stuff — never touched)
160  4     unown_table (u32)         (never touched)
164  40    GBA dual-slot: ruby x2, sapphire x2, emerald x2, firered x2, leafgreen x2 (u32) -> land slots 8,9
204  4     surf_rate
208  40    surf slots 0..4: (max u8, min u8, pad u16, species u32)   slot i at 208 + 8*i
248  44    unused water table (all zero in Platinum)
292  4+40  old rod  (rate at 292, slots at 296 + 8*i)
336  4+40  good rod (rate at 336, slots at 340 + 8*i)
380  4+40  super rod(rate at 380, slots at 384 + 8*i)
```
Land slot weights 20,20,10,10,10,10,5,5,4,4,1,1 %. Water slot weights: surf 60,30,5,4,1; old rod 60,30,5,4,1;
good rod 40,40,15,4,1; super rod 40,40,15,4,1. Great Marsh (Safari) replaces land 6,7 with the daily marsh mon;
Trophy Garden (post-NatDex) replaces land 6,7 with Mr. Backlot's daily mons.

### Python objects
```python
SPEC_VERSION = 1
NARC_PATH = '/fielddata/encountdata/pl_enc_data.narc'
TABLE_COUNT = 183; TABLE_SIZE = 424
SLOT_KINDS = {  # kind: (base offset, count, entry size, entry type)
  'land': (4, 12, 8, 'land'),       # entry = level u32, species u32
  'swarm': (100, 2, 4, 'species'), 'day': (108, 2, 4, 'species'), 'night': (116, 2, 4, 'species'),
  'radar': (124, 4, 4, 'species'),
  'ruby': (164, 2, 4, 'species'), 'sapphire': (172, 2, 4, 'species'), 'emerald': (180, 2, 4, 'species'),
  'firered': (188, 2, 4, 'species'), 'leafgreen': (196, 2, 4, 'species'),
  'surf': (208, 5, 8, 'water'), 'old_rod': (296, 5, 8, 'water'), 'good_rod': (340, 5, 8, 'water'),
  'super_rod': (384, 5, 8, 'water'),   # water entry = max u8, min u8, pad u16, species u32
}
RATE_OFFSETS = {'land': 0, 'surf': 204, 'old_rod': 292, 'good_rod': 336, 'super_rod': 380}

class SlotEdit(NamedTuple):
    table: int        # NARC member index 0..182 (== pret encounters.order line)
    name: str         # pret table name, e.g. 'route_201' (for humans/logs)
    kind: str         # key of SLOT_KINDS
    slot: int         # 0..count-1
    species: int      # national dex id (new)
    level: int | None # land: level; water: None
    min_level: int | None; max_level: int | None   # water only
    old_species: int  # vanilla value the engine must find there (refuse to patch otherwise)
    old_level: int | None; old_min: int | None; old_max: int | None   # vanilla levels (same check)

class RateEdit(NamedTuple):   # only if a table needs a rate turned on (expected: none or very few)
    table: int; name: str; kind: str; rate: int; old_rate: int

EDITS: tuple[SlotEdit, ...]
RATE_EDITS: tuple[RateEdit, ...]
def apply(member_index: int, data: bytes) -> bytes   # pure helper: patch one 424-byte member (checks old_*)
def edits_for(member_index) -> list
```
Engine only needs `EDITS`, `RATE_EDITS`, `SLOT_KINDS`, `RATE_OFFSETS`, `apply()`; the rest is for PLAN/tests.
`encdata_ex.narc` (honey trees, Trophy Garden daily list, Great Marsh daily list) is NOT edited in v1 (if that
changes I will add `EX_EDITS` and tell you here first).

### Events (also in sinnoh_complete.py)
```python
class EventUnlock(NamedTuple):
    id: str              # 'oaks_letter', 'member_card', 'azure_flute', ...
    item: int | None     # key item id to put in the bag (pret ITEM_*), None if only a flag/var
    flags: tuple         # (flag id, value) pairs the game's distribution would set (pret FLAG_*), if any
    vars: tuple          # (var id, value)
    requires: str        # condition in plain words + machine key, e.g. 'national_dex', 'hall_of_fame'
    gives: str           # what the player then gets through the game's own script
EVENTS: tuple[EventUnlock, ...]
```
Details (exact flags/vars/items from pret scripts) follow below once researched.

## sinnoh-design progress
- [x] table layout verified against the ROM (183 x 424, pret JSON == ROM, 0 mismatches)
- [x] obtainability audit (`python dev/sinnoh/check_plan.py --audit`)
- [x] PLAN.md (dev/sinnoh/PLAN.md) — for Ilias to review
- [x] sinnoh_complete.py — **SPEC_VERSION 2 (final): 77 slot edits, 123 area edits, digest 7480f186adbfd87f8d5139aad63dabcac4388906**
- [x] check_plan.py (exit 0) + client/tests/test_sinnoh_complete.py (20 tests)

### sinnoh-design -> engine (2026-10-26)
- **Spec v1 is final from my side** (until Ilias reviews PLAN.md; any change = SPEC_VERSION bump + note here).
  Please run `build_rom.py --update`. `apply()` is pure, raises ValueError naming table/kind/slot, leaves water pad
  bytes alone; `validate()` rejects duplicate (table, kind, slot). Extra helpers: `parse_table()`, `chances()`,
  `digest()`, `EARLY_UNTOUCHED`, `POST_GAME`. Edits use kinds land / day / night / good_rod / super_rod / surf only.
- Encounter kinds for Soul Link: nothing new (only species/levels inside existing slots change; no new kind, no rate
  change, no new map gets encounters).
- Events: agreed you own them. My PLAN proposal for the unlock moments (Ilias decides):
  Secret Key at "caught a Rotom" (or National Dex, whichever first), Oak's Letter + Member Card at National Dex
  (Darkrai still needs the game's own Lunar Wing quest), Azure Flute at National Dex *and* Dialga + Palkia caught
  (FLAG_CAUGHT_DIALGA / FLAG_CAUGHT_PALKIA — makes the Arceus trip the finale of the Spear Pillar story; Ilias may
  prefer plain National Dex).
- **Regigigas loop (FYI, not an event flag):** Platinum's Regirock/Regice/Registeel statues require a *fateful
  encounter* Regigigas in the party (`CheckPartyHasFatefulEncounterRegigigas`), and Snowpoint's Regigigas needs all
  three Regis in the party. Neither has an in-game start without an event Regigigas or a GBA game → listed as an
  option for Ilias (e.g. a one-time YellowLink gift of a fateful Regigigas, then the game's own ruins work).
- ROM has DP tables too (`d_enc_data.narc`/`p_enc_data.narc`, same layout): used only as design reference, never edited.

## engine notes
(sinnoh-engine: write here)

### sinnoh-engine (2026-10-26) — format: AGREED, a few requests
- Your v1 format is fine as written. I use: `SPEC_VERSION`, `NARC_PATH`, `TABLE_COUNT`, `TABLE_SIZE`, `SLOT_KINDS`,
  `RATE_OFFSETS`, `EDITS`, `RATE_EDITS`, `apply(member_index, data) -> bytes`. Please make `apply` pure + deterministic,
  raise `ValueError` (with table name/kind/slot) when an `old_*` value doesn't match, and leave the 2 pad bytes of water
  entries untouched. Several edits on the same (table, kind, slot) = error (I check that in the unit tests too).
- **Versioning:** bump `SPEC_VERSION` whenever EDITS/RATE_EDITS change and say so here. The builder
  (`client/yellowlink/sinnoh_rom.py`, mine) stores `EXPECTED = {spec digest: (ROM SHA1, SPEC_VERSION)}` (digest = `sinnoh_rom.spec_digest`, over version + EDITS + RATE_EDITS); a unit test
  fails if the spec's digest no longer matches → recompute with `python dev/sinnoh/build_rom.py --update` (prints the
  new SHA1 + digest to paste). Until your module exists I build from `dev/sinnoh/test_spec.py` (3 tiny edits).
- **Events: I own them** (runtime RAM writes, `client/yellowlink/sinnoh_events.py`), so you don't need `EventUnlock`
  in sinnoh_complete.py — reference them in PLAN.md if you like. Researched in pret (`src/scrcmd_mystery_gift.c` =
  exactly what the Mystery Gift deliveryman writes):
  | event | bag (key item) | var written by the gift | game's own requirement (scripts) |
  |---|---|---|---|
  | Member Card → Darkrai (Newmoon Island) | ITEM_MEMBER_CARD 454 | VAR_DISTRIBUTION_EVENT_DARKRAI 0x4043 = 0x1209 | FLAG_GAME_COMPLETED + National Dex + FLAG_WOKE_UP_..._LITTLE_BOY (Lunar Wing quest) → sleep in Canalave Harbor Inn |
  | Oak's Letter → Shaymin (Seabreak Path) | ITEM_OAKS_LETTER 452 | VAR_DISTRIBUTION_EVENT_SHAYMIN 0x4044 = 0x1112, VAR_SHAYMIN_EVENT_STATE 0x4057: 0→1 | game completed + National Dex → Route 224 (Oak + tablet) |
  | Azure Flute → Arceus (Hall of Origin) | ITEM_AZURE_FLUTE 455 | VAR_DISTRIBUTION_EVENT_ARCEUS 0x4045 = 0x1123 | game completed + National Dex → play it at Spear Pillar |
  | Secret Key → Rotom's room (Eterna Galactic building; Rotom forms) | ITEM_SECRET_KEY 467 | VAR_DISTRIBUTION_EVENT_ROTOM 0x4046 = 0x1103 | FLAG_TEAM_GALACTIC_LEFT_ETERNA_BUILDING; appliances need a Rotom in the party |
  Only other Mystery-Gift-only content: the Manaphy egg (mythical → excluded per brief). Pokétch apps: all in-game.
  Plan: give each at a safe frame once **National Dex** is obtained (Secret Key: once the player has caught a Rotom or
  has the National Dex — the Rotom room is pointless before), with a toast; then the game's own scripts run.
- FYI for PLAN.md: the Pokédex AREA map comes from a separate file (`zukan_enc_platinum.narc`, built from the same
  JSONs) which we do NOT edit → the dex area screen won't show the new spots. Listed as a decision for Ilias.
- I will also provide `client/yellowlink/encdata.py` (pure: parse a 424-byte table into dicts, NARC member offsets)
  — use it in check_plan.py if handy.
- (later) Your sinnoh_complete.py plugs in fine (build_rom.py builds it; 0 edits so far = vanilla bytes).
  `client/tests/test_sinnoh_rom.py::test_shipped_spec_has_a_checked_rom` fails until the SHA1 is recorded — that
  is on purpose. **Tell me here when the spec is final**; I run `build_rom.py --update` then (and again after every
  later change). Meanwhile my e2e runs use the test spec (YL_SINNOH_SPEC=test).

### sinnoh-design -> engine: **spec v2 is FINAL** (2026-10-26, after Ilias's review)
- `SPEC_VERSION = 2`, **77 slot edits** (32 tables), 0 rate edits, **123 Pokédex area edits** (111 area members).
  `sinnoh_complete.digest()` = `7480f186adbfd87f8d5139aad63dabcac4388906` (now covers AREA_EDITS too). Please
  run `build_rom.py --update`, and make your `spec_digest` include `AREA_EDITS` as well.
- Slot changes vs v1: the 12 starters moved from post-game routes to mid-game spots (Routes 206/208/209/211E/212N/
  215, Valor Lakefront, Fuego Ironworks, Lake Valor + Lake Acuity surf); Routes 224/225/227/229/230, Resort Area and
  Stark Mountain no longer change except Shuckle (224) and Trapinch (228). Same kinds as before (land/day/night/
  surf/good_rod/super_rod).
- **New: Pokédex area map** `/application/zukanlist/zkn_data/zukan_enc_platinum.narc` (4954 members). Members 0-3
  untouched; member `4 + species + 495*category` (`sc.area_member(species, category)`, categories in
  `sc.AREA_CATEGORIES`) = u32 map numbers, sorted, terminated by one 0 word. `sc.apply_area(member, data)` is pure,
  returns the new bytes (add/remove, re-sorted; raises ValueError if an add is already there / a remove is missing /
  bad terminator). `sc.area_members()` = the 111 members that change. **Members change size → the NARC (FAT + file
  in the ROM's FNT/FAT) has to be rebuilt**, unlike pl_enc_data. check_plan.py proves our re-implementation of
  Nintendo's area builder reproduces the ROM file exactly and that AREA_EDITS == builder(edited tables).
- Great Marsh: surf lists are never reachable (no field moves in the Safari game) — validate() rejects marsh surf edits.
- Ilias's other decisions that touch you: swarms rotate every 2 hours (shared), Regis/Regigigas loop opened by you,
  event moments as I proposed (Secret Key at first Rotom or NatDex; Oak's Letter + Member Card at NatDex; Azure
  Flute at NatDex + FLAG_CAUGHT_DIALGA + FLAG_CAUGHT_PALKIA).
