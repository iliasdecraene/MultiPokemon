"""Pokémon Platinum (US, rev 0) adapter: RAM access for the app (`self.G` when the game is Platinum).

Every address below was found with the pret/pokeplatinum decompilation and **verified on the real ROM**
(SHA1 below) by dev/platinum/verify.py; details and how each one was found: dev/platinum/MEMORY_MAP.md.
Pure Python, no pygame. `bus` = any object with read(addr, n) / r8 / r16 / r32 / write / w8 / w16 / w32
(client/yellowlink/nds_core.NDSCore, dev/platinum/ds.DS).

How the game's memory is organised (all main RAM, 0x02000000-0x023FFFFF):
  * Platinum heap-allocates everything, and the heap layout differs between "new game" and "continue" boots
    (e.g. SaveData at 0x0227E1CC vs 0x0227E1AC), so nothing below is a fixed heap address: we always follow one of
    three static pointers in the ARM9 binary's .bss:
      0x02101D28 sApplication (main.c): +0 currOverlayID, +4 currApplication (ApplicationManager*; this is the
                 "well-known cheat base pointer" 0x02101D2C), +0x10 args {unk_00, error, saveData (0x02101D40)}.
      0x02101D40 sApplication.args.saveData -> SaveData (valid from power-on, before the title screen).
      0x021C07DC sFieldSystem (field_system.c) -> FieldSystem while the game proper runs (NULL on boot/title/menu;
                 NOT cleared on teardown, so it is only trusted while the top-level app is the field system).
  * SaveData: +0 backupExists, +4 dataExists (1 = a save was loaded / has been written), +8 isNewGameData,
    +0x14 body (the 128 KiB save image, pages at fixed offsets below; the page table at +0x20024 agrees).
"""
from __future__ import annotations

import struct

PLATINUM_SHA1 = "ce81046eda7d232513069519cb2085349896dec7"   # Pokémon Platinum (USA) rev 0, "POKEMON PL" CPUE

RAM_LO, RAM_HI = 0x02000000, 0x02400000

# --- static pointers (ARM9 .bss) --------------------------------------------------------------------------
APP_CUR_OVERLAY = 0x02101D28      # sApplication.currOverlayID (77 title/intro movie, 97 main menu, -1 field)
APP_CUR = 0x02101D2C              # sApplication.currApplication (ApplicationManager*)
SAVE_DATA_PTR = 0x02101D40        # sApplication.args.saveData (SaveData*)
FIELD_SYSTEM_PTR = 0x021C07DC     # sFieldSystem (FieldSystem*)

# ApplicationManager: template {init, main, exit, overlayID} +0..+0xF, execState +0x10, procState +0x14,
# args +0x18, data +0x1C, parent +0x20, child +0x24.
FIELD_APP_MAIN = 0x0203CCCD       # ExecuteFieldProcesses (Thumb): main fn of both field-system templates
OVERLAY_GAME_OPENING = 77         # intro movie + "PRESS START" title screen
OVERLAY_MAIN_MENU = 97            # CONTINUE / NEW GAME menu
OVERLAY_BATTLE = 16               # child app while a battle runs (template 0x020EA358 in ARM9)
OVERLAY_PC_BOXES = 19             # child app = Pokémon Storage System UI (template 0x020EA2C8: BoxAppMan_*)
OVERLAY_CHOOSE_STARTER = 78
HOOK_PC_OPEN = 0x0203D754         # FieldSystem_OpenPokemonStorage (ARM9, Thumb): runs once when the box UI opens.
# Note: BoxAppMan_Init/Exit (0x021D0D80 / 0x021D0E58) live in overlay 19, whose load address is shared with
# other overlays (the field map overlay 5 runs code at 0x021D0D80 every frame) -> poll pc_open() instead.

# --- SaveData ---------------------------------------------------------------------------------------------
SAVE_DATA_EXISTS = 0x04
SAVE_BODY = 0x14
# page offsets inside the body (SaveData +0x20024 + 16*id: {id, size, offset, checksum, block})
PAGE_PLAYER = 0x0064              # PlayerSave: +0 Options (u16), +4 TrainerInfo
PAGE_PARTY = 0x0098               # Party: +0 capacity (6), +4 count, +8 6 x 236-byte Pokemon
PAGE_FIELD_PLAYER = 0x1280        # FieldOverworldState: +0 Location player {mapHeaderID, warpId, x, z, dir}
PAGE_PC_BOXES = 0xCF2C            # PCBoxes (save block 1, "boxes")
# TrainerInfo (PlayerSave+4): name u16[8] +0, id u32 +0x10 (TID low / SID high), money u32 +0x14, gender u8 +0x18,
# language +0x19, badgeMask +0x1A, appearance +0x1B, gameCode +0x1C
TRAINER = PAGE_PLAYER + 4
OPTIONS = PAGE_PLAYER             # u16: textSpeed bits 0-3 (0 slow, 1 mid, 2 fast) ...
# PCBoxes: currentBoxID u32 +0, boxMons [18][30] x 136 +4, names [18][20] u16 +0x11EE4, wallpapers [18] +0x121B4,
# unlockedWallpapers +0x121C6. Initialised by NEW GAME (names "BOX 1".."BOX 18", wallpapers 0..15,0,1, empty
# slots = 136 zero bytes encrypted with seed 0) -> always valid in game, no "first save" step like Crystal.
NUM_BOXES = 18
BOX_SLOTS = 30
BOX_MON_SIZE = 136
PARTY_MON_SIZE = 236
BOX_MONS = 0x4
BOX_NAMES = 0x4 + NUM_BOXES * BOX_SLOTS * BOX_MON_SIZE      # 0x11EE4
BOX_NAME_LEN = 20                                           # u16 chars incl. terminator
BOX_WALLPAPERS = BOX_NAMES + NUM_BOXES * BOX_NAME_LEN * 2   # 0x121B4

# --- FieldSystem ------------------------------------------------------------------------------------------
FS_PROCESS_MANAGER = 0x00         # FieldProcessManager*: {parent app (field map), child app, pause, kill}
FS_SAVE_DATA = 0x0C
FS_TASK = 0x10                    # FieldTask* : non-NULL while a script/event/menu/warp/text runs
FS_LOCATION = 0x1C                # Location* (= SaveData body + PAGE_FIELD_PLAYER)
FS_MAP_OBJ_MAN = 0x38
FS_PLAYER_AVATAR = 0x3C
FS_RUNNING_FIELD_MAP = 0x68       # BOOL
FS_MAP_LOAD_TYPE = 0x70           # 0 overworld, 1 underground, 2 colosseum, 3 union room, 4 battle tower
# PlayerAvatar: +0x08 movementAction, +0x14 moveState (0 idle, 1 moving, 2 turning), +0x1C avatar state
# (0 walking, 1 cycling, 2 surfing, ...), +0x20 gender, +0x30 MapObject*
PA_ACTION = 0x08
PA_MOVE_STATE = 0x14
PA_STATE = 0x1C
PA_GENDER = 0x20
PA_MAP_OBJECT = 0x30
# MapObject (0x128 bytes): +0x28 facingDir, +0x2C movingDir, +0x64/68/6C x/y/z (tiles; x,z updated at the START
# of a step = destination tile), +0x70 pos VecFx32 (x, y, z; 16 units per tile, tile centre = tile*16+8; moves
# smoothly: 1 unit/frame walking, 2 running), +0xA4 movementAction, +0xA8 movementStep.
MO_FACING = 0x28
MO_MOVING_DIR = 0x2C
MO_X, MO_Y, MO_Z = 0x64, 0x68, 0x6C
MO_POS = 0x70
MO_ACTION = 0xA4

DIR_UP, DIR_DOWN, DIR_LEFT, DIR_RIGHT = 0, 1, 2, 3     # the game's DIR_NORTH/SOUTH/WEST/EAST
RUN_ACTIONS = range(88, 92)                            # MOVEMENT_ACTION_RUN_NORTH..EAST

MAP_HEADERS = 0x020E601C          # sMapHeaders in ARM9: 593 x 24 bytes, mapLabelTextID at +18 (verified = table)
NUM_MAP_HEADERS = 593


def _ptr(v: int) -> bool:
    return RAM_LO <= v < RAM_HI


def _xor16(data: bytes, seed: int) -> bytes:
    out = bytearray(data)
    for i in range(0, len(out) - 1, 2):
        seed = (seed * 0x41C64E6D + 0x6073) & 0xFFFFFFFF
        out[i] ^= (seed >> 16) & 0xFF
        out[i + 1] ^= seed >> 24
    return bytes(out)


def record_checksum_ok(rec: bytes) -> bool:
    """True if an encrypted 136/236-byte Gen-4 record's checksum matches its (decrypted) data."""
    if len(rec) < BOX_MON_SIZE:
        return False
    chk = struct.unpack_from("<H", rec, 6)[0]
    plain = _xor16(rec[8:BOX_MON_SIZE], chk)
    return sum(struct.unpack("<64H", plain)) & 0xFFFF == chk


def _decode(codes) -> str:
    try:
        from .ndsrom import g4_decode
    except ImportError:      # pragma: no cover - only when used outside the package
        g4_decode = None
    if g4_decode:
        return g4_decode(codes)
    out = []
    for c in codes:
        if c == 0xFFFF:
            break
        if 0x121 <= c <= 0x12A:
            out.append(chr(48 + c - 0x121))
        elif 0x12B <= c <= 0x144:
            out.append(chr(65 + c - 0x12B))
        elif 0x145 <= c <= 0x15E:
            out.append(chr(97 + c - 0x145))
        else:
            out.append("?")
    return "".join(out)


class PlatinumMemory:
    """Same role as game.GameMemory / crystal.CrystalMemory, for Pokémon Platinum."""

    def __init__(self, bus):
        self.m = bus

    # raw access ------------------------------------------------------------------------------------------
    def r8(self, a):
        return self.m.r8(a)

    def r16(self, a):
        return self.m.r16(a)

    def r32(self, a):
        return self.m.r32(a)

    def read(self, a, n):
        return self.m.read(a, n)

    # pointers --------------------------------------------------------------------------------------------
    def save_data(self):
        """Address of SaveData (valid from power-on) or None."""
        sd = self.m.r32(SAVE_DATA_PTR)
        return sd if _ptr(sd) else None

    def save_body(self):
        sd = self.save_data()
        return sd + SAVE_BODY if sd else None

    def top_overlay(self) -> int:
        """sApplication.currOverlayID: 77 intro/title, 97 main menu, -1 (0xFFFFFFFF) field / Rowan's intro."""
        v = self.m.r32(APP_CUR_OVERLAY)
        return v - (1 << 32) if v & 0x80000000 else v

    def field_system(self):
        """FieldSystem address while the game proper (overworld + its battles/menus/apps) runs, else None."""
        fs = self.m.r32(FIELD_SYSTEM_PTR)
        if not _ptr(fs):
            return None
        app = self.m.r32(APP_CUR)
        if not _ptr(app) or self.m.r32(app + 4) != FIELD_APP_MAIN or self.m.r32(app + 0x1C) != fs:
            return None
        sd = self.save_data()
        if sd is None or self.m.r32(fs + FS_SAVE_DATA) != sd:
            return None
        return fs

    def _procs(self, fs):
        pm = self.m.r32(fs + FS_PROCESS_MANAGER)
        if not _ptr(pm):
            return 0, 0, 1
        parent, child, pause = struct.unpack("<3I", self.m.read(pm, 12))
        return parent, child, pause

    def child_overlay(self):
        """Overlay id of the field's child app (16 battle, 19 PC boxes, bag/party/summary/...) or None."""
        fs = self.field_system()
        if fs is None:
            return None
        _parent, child, _pause = self._procs(fs)
        if not _ptr(child):
            return None
        return self.m.r32(child + 0x0C)

    def player_avatar(self):
        fs = self.field_system()
        if fs is None:
            return None
        pa = self.m.r32(fs + FS_PLAYER_AVATAR)
        return pa if _ptr(pa) else None

    def player_object(self):
        """The player's MapObject address (localID 0xFF) or None."""
        pa = self.player_avatar()
        if pa is None:
            return None
        mo = self.m.r32(pa + PA_MAP_OBJECT)
        return mo if _ptr(mo) else None

    # state checks ----------------------------------------------------------------------------------------
    def has_save(self) -> bool:
        """The cartridge has a save (SaveData.dataExists; set at boot when one was found, and by the first save)."""
        sd = self.save_data()
        return bool(sd and self.m.r32(sd + SAVE_DATA_EXISTS))

    def in_game(self) -> bool:
        """The game proper runs (after CONTINUE or the new-game intro): false on boot, intro, title, main menu."""
        return self.field_system() is not None

    def save_loaded(self) -> bool:
        """The save data in RAM belongs to the running game (CONTINUE'd save or a fresh NEW GAME) = in_game().
        Platinum's PC boxes are valid in RAM from then on (NEW GAME initialises them; no first-save step)."""
        return self.in_game()

    def in_battle(self) -> bool:
        return self.child_overlay() == OVERLAY_BATTLE

    def pc_open(self) -> bool:
        """Pokémon Storage System box UI is open (deposit/withdraw/move/move items). Deposits/withdrawals change the
        PC boxes / party in the save RAM immediately; the UI is gone again when this turns False."""
        return self.child_overlay() == OVERLAY_PC_BOXES

    def field_task(self) -> bool:
        """A field task runs: script/event, text box, start menu, warp/door transition, PC menu, battle intro..."""
        fs = self.field_system()
        return bool(fs and self.m.r32(fs + FS_TASK))

    def overworld_running(self) -> bool:
        """The field map runs with no child app (battle/menu screens) and is not paused."""
        fs = self.field_system()
        if fs is None:
            return False
        parent, child, pause = self._procs(fs)
        return bool(_ptr(parent) and not child and not pause and self.m.r32(fs + FS_RUNNING_FIELD_MAP))

    def walking(self) -> bool:
        """The player is turning or between tiles."""
        pa = self.player_avatar()
        mo = self.player_object()
        if pa is None or mo is None:
            return False
        if self.m.r32(pa + PA_MOVE_STATE) != 0:
            return True
        x, _y, z = struct.unpack("<3i", self.m.read(mo + MO_X, 12))
        px, _py, pz = struct.unpack("<3i", self.m.read(mo + MO_POS, 12))
        return (px >> 12) != x * 16 + 8 or (pz >> 12) != z * 16 + 8

    def is_safe(self) -> bool:
        """Standing still in the overworld, player in control: no script/text/menu/warp, battle or other screen.
        (The game's own input condition: !pause && runningFieldMap && no FieldTask; plus not moving.)"""
        return (self.overworld_running() and not self.field_task() and self.player_object() is not None
                and not self.walking())

    def overworld_visible(self) -> bool:
        return self.overworld_running()

    # position --------------------------------------------------------------------------------------------
    def map_id(self) -> int:
        """Map header id (enum MapHeaderID, 0..592; e.g. 411 Twinleaf Town, 342 Route 201) or 0 if not in game.
        Outdoor maps share one big matrix with global tile coordinates, so neighbouring outdoor maps have
        continuous x/y."""
        fs = self.field_system()
        if fs is None:
            return 0
        loc = self.m.r32(fs + FS_LOCATION)
        return self.m.r32(loc) if _ptr(loc) else 0

    def position(self) -> dict:
        """Player position.

        x, y   tile coords (y = the DS z axis; = destination tile while stepping, like the game's own coords)
        h      height (MapObject y)
        px, py smooth position in 1/16 tile units, = x*16 / y*16 when standing (pos - 8: pos is the tile centre)
        ph     smooth height (pos.y in the same units; observed pos.y = 8*h)
        facing 0 up, 1 down, 2 left, 3 right (game DIR_*); dir = moving direction
        moving bool, running bool (RUN actions), action = MovementAction, state = 0 walk/1 bike/2 surf...
        gender 0 boy / 1 girl
        """
        mo = self.player_object()
        pa = self.player_avatar()
        if mo is None or pa is None:
            return {"map": self.map_id(), "x": 0, "y": 0, "h": 0, "px": 0, "py": 0, "ph": 0, "facing": DIR_DOWN,
                    "dir": DIR_DOWN, "moving": False, "running": False, "action": 0, "state": 0,
                    "gender": self.gender()}
        x, h, z = struct.unpack("<3i", self.m.read(mo + MO_X, 12))
        px, ph, pz = struct.unpack("<3i", self.m.read(mo + MO_POS, 12))
        action = self.m.r32(mo + MO_ACTION)
        return {"map": self.map_id(), "x": x, "y": z, "h": h,
                "px": (px >> 12) - 8, "py": (pz >> 12) - 8, "ph": ph >> 12,
                "facing": self.m.r32(mo + MO_FACING) & 3, "dir": self.m.r32(mo + MO_MOVING_DIR) & 3,
                "moving": self.walking(), "running": action in RUN_ACTIONS, "action": action,
                "state": self.m.r32(pa + PA_STATE), "gender": self.m.r32(pa + PA_GENDER) & 1}

    # trainer ---------------------------------------------------------------------------------------------
    def _trainer(self, off, n):
        body = self.save_body()
        return self.m.read(body + TRAINER + off, n) if body else bytes(n)

    def player_name(self) -> str:
        return _decode(struct.unpack("<8H", self._trainer(0, 16)))

    def trainer_id(self) -> int:
        """Visible trainer ID (TID, low 16 bits of TrainerInfo.id)."""
        return struct.unpack("<H", self._trainer(0x10, 2))[0]

    def secret_id(self) -> int:
        return struct.unpack("<H", self._trainer(0x12, 2))[0]

    def money(self) -> int:
        return struct.unpack("<I", self._trainer(0x14, 4))[0]

    def gender(self) -> int:
        return self._trainer(0x18, 1)[0]

    def badge_mask(self) -> int:
        return self._trainer(0x1A, 1)[0]

    def badges(self) -> int:
        """Number of Sinnoh badges (popcount of TrainerInfo.badgeMask)."""
        return bin(self.badge_mask()).count("1")

    # party -----------------------------------------------------------------------------------------------
    def party_count(self) -> int:
        body = self.save_body()
        if not body:
            return 0
        n = self.m.r32(body + PAGE_PARTY + 4)
        return n if 0 <= n <= 6 else 0

    def party_addr(self, i: int):
        body = self.save_body()
        return body + PAGE_PARTY + 8 + i * PARTY_MON_SIZE if body else None

    def party_raw(self) -> list:
        """The party as encrypted 236-byte records (pk4.decrypt)."""
        body = self.save_body()
        if not body:
            return []
        return [self.m.read(body + PAGE_PARTY + 8 + i * PARTY_MON_SIZE, PARTY_MON_SIZE)
                for i in range(self.party_count())]

    def party_checked(self):
        """party_raw() if every record's checksum is valid and two reads agree, else None (mid-update)."""
        a = self.party_raw()
        if a != self.party_raw() or not all(record_checksum_ok(r) for r in a):
            return None
        return a

    # PC boxes --------------------------------------------------------------------------------------------
    def box_base(self):
        """Address of the PCBoxes struct (currentBoxID u32 at +0, 18x30 x 136-byte encrypted mons at +4) or None
        when not in game. Use box_slot_addr() for a slot."""
        if not self.in_game():
            return None
        return self.save_body() + PAGE_PC_BOXES

    def pc_boxes_addr(self):
        """PCBoxes address regardless of in_game() (the save is in RAM from boot, e.g. on the title screen)."""
        body = self.save_body()
        return body + PAGE_PC_BOXES if body else None

    def box_slot_addr(self, box: int, slot: int):
        base = self.pc_boxes_addr()
        return base + BOX_MONS + (box * BOX_SLOTS + slot) * BOX_MON_SIZE if base else None

    def current_box(self) -> int:
        """Box shown when the PC opens (0-17); changes live while switching boxes in the UI."""
        base = self.pc_boxes_addr()
        return self.m.r32(base) if base else 0

    def box_raw(self, box: int) -> list:
        """30 encrypted 136-byte records of a box (empty slot = pk4.EMPTY_BOX)."""
        a = self.box_slot_addr(box, 0)
        if a is None:
            return []
        data = self.m.read(a, BOX_SLOTS * BOX_MON_SIZE)
        return [data[i * BOX_MON_SIZE:(i + 1) * BOX_MON_SIZE] for i in range(BOX_SLOTS)]

    def box_name(self, box: int) -> str:
        base = self.pc_boxes_addr()
        if not base:
            return ""
        return _decode(struct.unpack("<20H", self.m.read(base + BOX_NAMES + box * BOX_NAME_LEN * 2, 40)))

    def box_wallpaper(self, box: int) -> int:
        base = self.pc_boxes_addr()
        return self.m.r8(base + BOX_WALLPAPERS + box) if base else 0

    def status_line(self) -> str:
        """One-line game state for logs / F3 reports."""
        p = self.position()
        return (f"in_game={self.in_game()} ov={self.top_overlay()} child={self.child_overlay()} map={p['map']} "
                f"xy=({p['x']},{p['y']}) task={self.field_task()} safe={self.is_safe()} battle={self.in_battle()} "
                f"pc={self.pc_open()} walk={p['moving']} party={self.party_count()} save={self.has_save()}")

    def write_box_slot(self, box: int, slot: int, rec: bytes):
        """Write one encrypted 136-byte record. Only call while is_safe() (or the PC UI is closed)."""
        assert len(rec) == BOX_MON_SIZE
        self.m.write(self.box_slot_addr(box, slot), bytes(rec))


def party_records(pm: PlatinumMemory) -> list:
    """Party as 136-byte encrypted box records (the first 136 bytes of a party record are a valid box record)."""
    return [r[:BOX_MON_SIZE] for r in pm.party_raw()]


# ---------------------------------------------------------------------------------------------------------
# Areas (location names). map id -> location label = sMapHeaders[id].mapLabelTextID (pret data/map_headers.h,
# checked against the ROM's own table at MAP_HEADERS for all 593 maps by dev/platinum/verify.py); names are the
# location_names message bank (en_US).
# ---------------------------------------------------------------------------------------------------------
LOCATIONS = (
    ('mystery_zone', 'Mystery Zone'), ('twinleaf_town', 'Twinleaf Town'), ('sandgem_town', 'Sandgem Town'),
    ('floaroma_town', 'Floaroma Town'), ('solaceon_town', 'Solaceon Town'), ('celestic_town', 'Celestic Town'),
    ('jubilife_city', 'Jubilife City'), ('canalave_city', 'Canalave City'), ('oreburgh_city', 'Oreburgh City'),
    ('eterna_city', 'Eterna City'), ('hearthome_city', 'Hearthome City'), ('pastoria_city', 'Pastoria City'),
    ('veilstone_city', 'Veilstone City'), ('sunyshore_city', 'Sunyshore City'),
    ('snowpoint_city', 'Snowpoint City'), ('pokemon_league', 'Pokémon League'), ('route_201', 'Route 201'),
    ('route_202', 'Route 202'), ('route_203', 'Route 203'), ('route_204', 'Route 204'), ('route_205', 'Route 205'),
    ('route_206', 'Route 206'), ('route_207', 'Route 207'), ('route_208', 'Route 208'), ('route_209', 'Route 209'),
    ('route_210', 'Route 210'), ('route_211', 'Route 211'), ('route_212', 'Route 212'), ('route_213', 'Route 213'),
    ('route_214', 'Route 214'), ('route_215', 'Route 215'), ('route_216', 'Route 216'), ('route_217', 'Route 217'),
    ('route_218', 'Route 218'), ('route_219', 'Route 219'), ('route_220', 'Route 220'), ('route_221', 'Route 221'),
    ('route_222', 'Route 222'), ('route_223', 'Route 223'), ('route_224', 'Route 224'), ('route_225', 'Route 225'),
    ('route_226', 'Route 226'), ('route_227', 'Route 227'), ('route_228', 'Route 228'), ('route_229', 'Route 229'),
    ('route_230', 'Route 230'), ('oreburgh_mine', 'Oreburgh Mine'), ('valley_windworks', 'Valley Windworks'),
    ('eterna_forest', 'Eterna Forest'), ('fuego_ironworks', 'Fuego Ironworks'), ('mt_coronet', 'Mt. Coronet'),
    ('spear_pillar', 'Spear Pillar'), ('great_marsh', 'Great Marsh'), ('solaceon_ruins', 'Solaceon Ruins'),
    ('victory_road', 'Victory Road'), ('pal_park', 'Pal Park'), ('amity_square', 'Amity Square'),
    ('ravaged_path', 'Ravaged Path'), ('floaroma_meadow', 'Floaroma Meadow'), ('oreburgh_gate', 'Oreburgh Gate'),
    ('fullmoon_island', 'Fullmoon Island'), ('sendoff_spring', 'Sendoff Spring'),
    ('turnback_cave', 'Turnback Cave'), ('flower_paradise', 'Flower Paradise'),
    ('snowpoint_temple', 'Snowpoint Temple'), ('wayward_cave', 'Wayward Cave'),
    ('ruin_maniac_cave', 'Ruin Maniac Cave'), ('maniac_tunnel', 'Maniac Tunnel'),
    ('trophy_garden', 'Trophy Garden'), ('iron_island', 'Iron Island'), ('old_chateau', 'Old Chateau'),
    ('galactic_hq', 'Galactic HQ'), ('verity_lakefront', 'Verity Lakefront'),
    ('valor_lakefront', 'Valor Lakefront'), ('acuity_lakefront', 'Acuity Lakefront'),
    ('spring_path', 'Spring Path'), ('lake_verity', 'Lake Verity'), ('lake_valor', 'Lake Valor'),
    ('lake_acuity', 'Lake Acuity'), ('newmoon_island', 'Newmoon Island'), ('battle_tower', 'Battle Tower'),
    ('fight_area', 'Fight Area'), ('survival_area', 'Survival Area'), ('resort_area', 'Resort Area'),
    ('stark_mountain', 'Stark Mountain'), ('seabreak_path', 'Seabreak Path'), ('hall_of_origin', 'Hall of Origin'),
    ('verity_cavern', 'Verity Cavern'), ('valor_cavern', 'Valor Cavern'), ('acuity_cavern', 'Acuity Cavern'),
    ('jubilife_tv', 'Jubilife TV'), ('poketch_co', 'Pokétch Co.'), ('gts', 'GTS'),
    ('trainers_school', 'Trainers’ School'), ('mining_museum', 'Mining Museum'), ('flower_shop', 'Flower Shop'),
    ('cycle_shop', 'Cycle Shop'), ('contest_hall', 'Contest Hall'), ('poffin_house', 'Poffin House'),
    ('foreign_building', 'Foreign Building'), ('pokemon_day_care', 'Pokémon Day Care'),
    ('veilstone_store', 'Veilstone Store'), ('game_corner', 'Game Corner'),
    ('canalave_library', 'Canalave Library'), ('vista_lighthouse', 'Vista Lighthouse'),
    ('sunyshore_market', 'Sunyshore Market'), ('pokemon_mansion', 'Pokémon Mansion'),
    ('footstep_house', 'Footstep House'), ('cafe', 'Café'), ('grand_lake', 'Grand Lake'),
    ('restaurant', 'Restaurant'), ('battle_park', 'Battle Park'), ('battle_frontier', 'Battle Frontier'),
    ('battle_factory', 'Battle Factory'), ('battle_castle', 'Battle Castle'), ('battle_arcade', 'Battle Arcade'),
    ('battle_hall', 'Battle Hall'), ('distortion_world', 'Distortion World'),
    ('global_terminal', 'Global Terminal'), ('villa', 'Villa'), ('battleground', 'Battleground'),
    ('rotoms_room', 'ROTOM’s Room'), ('team_galactic_eterna_building', 'T.G. Eterna Bldg'),
    ('iron_ruins', 'Iron Ruins'), ('iceberg_ruins', 'Iceberg Ruins'), ('rock_peak_ruins', 'Rock Peak Ruins'),
)
MAP_LOCATION = bytes.fromhex(
    "00000006060606065b5b5b5a5a5a5a5a5a5a5a060606060606060606765d0606060707070707676767070707070808080808"
    "0808080808080808085e0808080808090909090909607a7a7a7a090909091509090909090a0a0a0a0a0a0a0a0a0a0a0a0a0a"
    "0a0a0a0a0a0a0a0a0a17181b0a0a0a0a626161630b0b0b0b0b0b0b0b0b0b0b0b0c0c0c0c666565656565650c0c0c0c0c0c1e"
    "0d0d0d0d0d0d0d690d0d0d0d0d0d680e0e0e0e0e0e0e0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f515151516f285151002e2e2e"
    "2f2f3030313100323232323232323232323232323333000b0035353535353535353535353535353535353500363636363636"
    "0037003839003a3a3b3b3c3c54545454003d3e3e3e3e3e3e3f00000040404040404041414244454545454545454646464646"
    "46464646004747474747474c4c574d4d584e4e594f4f6f6f000050505050505000004848496e6d6d4a4b1011121313141414"
    "1515001617171818181818181919191a1a1b6a6a6a1b1b1c1c6b6d6d6d6d1d1d1e1f1f202020212121222437242525252527"
    "2800002a00002b2c000000010101010101010202020202020202030303035f03030404040464040404040505050505050505"
    "5252525278525253535353535353775300232629002d5500060708090a0b0c0d0e0f51020304055253076c50470f0f472829"
    "2a2b2b2b34343434343456564243453568063e3e3e3e3e3e3e3e3e3e3e3e3e3e3e3e3e3e3e3e3e3e3e3e3e3e3e3e3e3e3e3e"
    "3e3e3e3e3e3e3e3e61707050717472736576764775797a7575757575757575757575333306457b327c2b7d"
)
LOCATION_NAMES = dict(LOCATIONS)


def area_of(map_id: int) -> str:
    """Area key of a map = its location label (e.g. 'twinleaf_town' for the town and every house in it)."""
    if 0 <= map_id < len(MAP_LOCATION):
        return LOCATIONS[MAP_LOCATION[map_id]][0]
    return "map_%04x" % map_id


def area_name(area: str) -> str:
    return LOCATION_NAMES.get(area, area.replace("_", " ").title())


# ---------------------------------------------------------------------------------------------------------
# Boot: title screen -> CONTINUE
# ---------------------------------------------------------------------------------------------------------
def _press(core, key: str, frames: int = 4):
    if hasattr(core, "press"):                          # dev/platinum/ds.DS
        try:
            core.press(key, 2)
        except TypeError:
            core.press(key)
        return
    core.button_press(key)                              # nds_core.NDSCore
    _run(core, frames)
    core.button_release(key)


def _run(core, n: int):
    if hasattr(core, "run"):
        core.run(n)
    else:
        core.tick(n, False) if _tick_takes_render(core) else core.tick(n)


def _tick_takes_render(core) -> bool:
    try:
        import inspect
        return "render" in inspect.signature(core.tick).parameters
    except (TypeError, ValueError):
        return False


def fast_continue(core, on_progress=None, max_frames: int = 7200) -> str:
    """From power-on: skip the intro movie + title screen and pick CONTINUE. Returns 'loaded' (player in control in
    the overworld), 'no save' (returned as soon as the intro starts, without pressing anything: with no save,
    START on the title screen leads straight into the new-game intro) or 'timeout'.

    Verified (dev/platinum/verify.py): START/A are only pressed while sApplication.currOverlayID is 77
    (game_opening: logos, intro movie, PRESS START), so a stray press can never pick NEW GAME; on the main menu
    (overlay 97) CONTINUE is the default entry -> A, but only if SaveData.dataExists.
    `core` needs press(key)/run(frames) (ds.DS) or button_press/button_release/tick (NDSCore) + the bus methods.
    """
    pm = PlatinumMemory(core)
    f = 0
    last_press = -999
    menu_since = None
    in_game_since = None
    toggle = 0
    while f < max_frames:
        if pm.in_game():
            # wait for the map fade-in / load task to finish (or give up waiting if a script keeps running)
            if in_game_since is None:
                in_game_since = f
            if pm.is_safe() or f - in_game_since > 900:
                _run(core, 10)
                return "loaded"
        else:
            ov = pm.top_overlay()
            if ov in (OVERLAY_GAME_OPENING, OVERLAY_MAIN_MENU) and not pm.has_save():
                # without a save the main menu jumps straight into NEW GAME (main_menu.c: isNewGame ->
                # NEXT_APP_GAME_INTRO), so never press anything: the game keeps looping intro <-> title.
                return "no save"
            if ov == OVERLAY_GAME_OPENING and f - last_press >= 30:
                _press(core, ("start", "a")[toggle])
                toggle ^= 1
                last_press = f
                f += 6
            elif ov == OVERLAY_MAIN_MENU:
                if menu_since is None:
                    menu_since = f
                if f - menu_since >= 40 and f - last_press >= 60:
                    _press(core, "a")
                    last_press = f
                    f += 6
            else:
                menu_since = None
        _run(core, 1)
        f += 1
        if on_progress and f % 120 == 0:
            on_progress()
    return "timeout"
