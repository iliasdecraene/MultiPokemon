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

from . import pk4

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

# --- Soul Link (verified by dev/platinum/soullink_verify.py; dev/notes/soullink_platinum.md) ----------------
# Battle: the battle child app (overlay 16): args (+0x18) = FieldBattleDTO, data (+0x1C) = BattleSystem.
DTO_PARTIES = 0x04                # Party *parties[4] (0 player, 1 enemy, 2 partner/player 2, 3 enemy 2)
DTO_MAP_LABEL = 0x158             # mapLabelTextID
DTO_MAP_HEADER = 0x15C            # mapHeaderID
DTO_PC_BOXES = 0x114              # PCBoxes* (= save body + PAGE_PC_BOXES in every DTO the field builds)
DTO_SAVE_DATA = 0x1C0             # SaveData* (not set for the catching tutorial)
BS_BATTLE_TYPE = 0x2C             # BattleSystem.battleType
BS_CTX = 0x30                     # BattleSystem.battleCtx (BattleContext*)
BS_PARTIES = 0x68                 # BattleSystem.parties[4] (the battle's own copies)
CTX_SELECTED_SLOT = 0x219C        # BattleContext.selectedPartySlot[4] (index into the battle party)
CTX_BATTLE_MONS = 0x2D40          # BattleContext.battleMons[4], 0xC0 bytes each
BATTLE_MON_SIZE = 0xC0
BM_SPECIES, BM_CUR_HP, BM_MAX_HP, BM_PID = 0x00, 0x4C, 0x50, 0x68
# BATTLE_TYPE_* (pret include/constants/battle.h)
BATTLE_TYPE_TRAINER, BATTLE_TYPE_DOUBLES, BATTLE_TYPE_LINK, BATTLE_TYPE_2VS2 = 0x1, 0x2, 0x4, 0x8
BATTLE_TYPE_TAG, BATTLE_TYPE_SAFARI, BATTLE_TYPE_AI, BATTLE_TYPE_FRONTIER = 0x10, 0x20, 0x40, 0x80
BATTLE_TYPE_ROAMER, BATTLE_TYPE_PAL_PARK, BATTLE_TYPE_CATCH_TUTORIAL = 0x100, 0x200, 0x400
BATTLE_TYPE_DEBUG = 0x80000000
SPECIAL_BATTLE_TYPES = (BATTLE_TYPE_LINK | BATTLE_TYPE_FRONTIER | BATTLE_TYPE_PAL_PARK | BATTLE_TYPE_CATCH_TUTORIAL
                        | BATTLE_TYPE_DEBUG)
KNOWN_BATTLE_TYPE_BITS = 0x7FF | BATTLE_TYPE_DEBUG
# battles where battle_party_slot() reports nothing: not the player's real party / no player mon sent out
NO_PLAYER_MON_TYPES = (BATTLE_TYPE_LINK | BATTLE_TYPE_FRONTIER | BATTLE_TYPE_PAL_PARK | BATTLE_TYPE_CATCH_TUTORIAL
                       | BATTLE_TYPE_SAFARI | BATTLE_TYPE_DEBUG)
# Save body pages (page table at SaveData+0x20024)
PAGE_BAG = 0x0630                 # Bag: items 165, key 50, TMs 100, mail 12, medicine 40, berries 64, balls 15, battle 30
BAG_BALLS = 0x6BC                 # Bag.pokeballs[15] = {u16 item, u16 quantity}
BALL_POCKET_SIZE = 15
BALL_FIRST, BALL_LAST = 1, 16     # ITEM_MASTER_BALL .. ITEM_CHERISH_BALL
PAGE_VARS_FLAGS = 0x0DAC          # VarsFlags: u16 vars[288] (VARS_START 16384..), then u8 flags[2912 / 8]
VF_FLAGS = 0x240
VARS_START, VARS_END, NUM_FLAGS = 16384, 16672, 2912
FLAG_HAS_PARTNER = 2401           # walking with Cheryl/Mira/Riley/Marley/Buck (wild battles are doubles)
FLAG_GAME_COMPLETED = 2404        # set by ClearGame (the Hall of Fame script)
FLAG_SAFARI_GAME_ACTIVE = 2407    # Great Marsh
VAR_PARTNER_TRAINER_ID = 16447
PAGE_DAYCARE = 0x1654             # Daycare: DaycareMon[2] {BoxPokemon 136, DaycareMail, u32 steps}, ...
DAYCARE_MON_SIZE = 0xEC           # 236 (2 x 236 + offspring PID u32 + step counter, padded: page 0x1E8)
PAGE_GAME_RECORDS = 0x61B0        # GameRecords: u32[71], u16[77], pad, EncodingSeed {u16 byteSum, u16 modifier}
GAME_RECORDS_SIZE = 0x1BC
RECORD_TIMES_ENTERED_HALL_OF_FAME = 73
HALL_OF_FAME_MAP = 187            # MAP_HEADER_POKEMON_LEAGUE_HALL_OF_FAME
MAIL_FIRST, MAIL_LAST = 137, 148  # ITEM_GRASS_MAIL .. ITEM_BRICK_MAIL
SPECIES_SHAYMIN = 492


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


def party_section_ok(rec: bytes) -> bool:
    """The 100-byte party section of an encrypted 236-byte record decrypts (LCRNG seeded by the PID) to
    plausible values: level 1-100, max HP > 0, HP <= max HP, mail type none or valid. A section the game has
    decrypted in place right now fails this (verified on the real game: dev/platinum/soullink_verify.py)."""
    if len(rec) < PARTY_MON_SIZE:
        return False
    sec = _xor16(rec[BOX_MON_SIZE:PARTY_MON_SIZE], struct.unpack_from("<I", rec, 0)[0])
    hp, max_hp = struct.unpack_from("<HH", sec, 6)
    return 1 <= sec[4] <= 100 and 0 < max_hp and hp <= max_hp and (sec[0x1B] == 0xFF or sec[0x1B] < 12)


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

    def __init__(self, bus, rom=None):
        self.m = bus
        self.rom = rom          # ndsrom.PlatinumData: needed only by the Soul Link forced moves (move PP, stats)
        self._obs = None        # (frame, snapshot) of the last clean observation (see observe_stable)

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
        """party_raw() if every record's checksum is valid, every party section (HP/level/stats, encrypted
        separately with the PID and not covered by the checksum) decrypts to something plausible, and two reads
        agree; else None (mid-update: the game decrypts records in place - in the overworld it does so with
        the lead of a big party every few frames, sometimes only the party section - so retry next frame)."""
        a = self.party_raw()
        if a != self.party_raw() or not all(record_checksum_ok(r) and party_section_ok(r) for r in a):
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

    # =====================================================================================================
    # Soul Link (Nuzlocke) helpers -- same names/shapes as crystal.py's Soul Link section.
    # Verified on the real ROM by dev/platinum/soullink_verify.py; details in dev/notes/soullink_platinum.md.
    # =====================================================================================================
    # Soul Link: battle --------------------------------------------------------------------------------------
    def _battle_app(self):
        """The battle child app (ApplicationManager, overlay 16) of the field system, or None."""
        fs = self.field_system()
        if fs is None:
            return None
        _parent, child, _pause = self._procs(fs)
        if not _ptr(child) or self.m.r32(child + 0x0C) != OVERLAY_BATTLE:
            return None
        return child

    def battle_dto(self):
        """Address of this battle's FieldBattleDTO (the battle app's args), or None outside battles. Built by the
        field before the battle app starts (wild mon / trainer party already in it) and freed after the encounter
        task has copied the party back into the save."""
        app = self._battle_app()
        if app is None:
            return None
        dto = self.m.r32(app + 0x18)
        if not _ptr(dto) or self.m.r32(dto) & ~KNOWN_BATTLE_TYPE_BITS:
            return None
        return dto

    def battle_type(self):
        """FieldBattleDTO.battleType (BATTLE_TYPE_* bit mask) or None outside battles."""
        dto = self.battle_dto()
        return self.m.r32(dto) if dto else None

    def battle_kind(self) -> str | None:
        """'wild' | 'trainer' | 'special' or None outside battles. Right from the battle app's first frame (the
        type is fixed when the field builds the DTO).

        special = no encounter can count and/or not the player's own party: catching tutorial (Dawn/Lucas),
        Pal Park, link, Battle Tower/Frontier, debug, and trainer battles with an AI partner (Cheryl, Mira,
        Riley, Marley, Buck; faints there are still real). Wild = grass/surf/fishing/honey tree/scripted
        statics and legendaries, roamers, the Great Marsh (safari) and wild double battles while walking with a
        partner (BATTLE_TYPE_AI_PARTNER, no trainer bit). Trainer = everything else with the trainer bit
        (incl. two-trainer double battles, BATTLE_TYPE_TAG without partner)."""
        bt = self.battle_type()
        if bt is None:
            return None
        return battle_kind_of(bt)

    def _party_mon(self, party_ptr: int, slot: int):
        """Plain bytes (pk4 plain order) of a mon in a Party struct {capacity, count, 6 x 236} or None."""
        if not _ptr(party_ptr):
            return None
        n = self.m.r32(party_ptr + 4)
        if not 0 <= slot < min(n, 6):
            return None
        return _plain_mon(self.m.read(party_ptr + 8 + slot * PARTY_MON_SIZE, PARTY_MON_SIZE))

    def battle_info(self) -> dict | None:
        """None outside battles; else the encounter as the Soul Link referee needs it:
          kind     'wild' | 'trainer' | 'special' (battle_kind)
          dex      national dex number (Gen-4 species ids are dex numbers) of the wild mon -- in a wild double
                   battle (walking with a partner) the FIRST wild mon (DTO enemy party 1 slot 0); for trainers
                   their first mon
          shiny    Gen-4 rule: (TID ^ SID ^ PID_hi ^ PID_lo) < 8 with the wild mon's own OT ids (= the player's)
          safari   True in the Great Marsh (BATTLE_TYPE_SAFARI); contest always False
          level    the wild mon's level
        Platinum extras: battle_type (bit mask), pid, key (= mon_key the mon will have once caught: PID+TID+SID
        don't change on capture), map (DTO mapHeaderID), others (dex of the other wild mon(s) in a double),
        keys / dexes (lists for ALL wild mons of the battle, first = key / dex; a trainer battle lists only its
        first mon) - in a double the player may catch the second wild mon: match the catch by key.
        None for a frame if a wild mon's record is being worked on right then (never seen; the DTO copies are
        only touched while the battle starts) - like "no battle" for a frame, which the referee tolerates.
        Valid from the battle app's first frame to its last: the field builds the DTO (and the wild mon) before
        the battle starts, and every battle has a fresh DTO -> never a mon from the previous battle."""
        dto = self.battle_dto()
        if dto is None:
            return None
        bt = self.m.r32(dto)
        kind = battle_kind_of(bt)
        mons = []
        for battler in (1, 3):                          # BATTLER_ENEMY_1 / BATTLER_ENEMY_2
            party = self.m.r32(dto + DTO_PARTIES + 4 * battler)
            n = self.m.r32(party + 4) if _ptr(party) else 0
            for slot in range(min(n, 6) if battler == 1 or kind == "wild" else 0):
                p = self._party_mon(party, slot)
                if p is None:
                    return None                         # being worked on this very frame: read again next frame
                mons.append(p)
                if kind != "wild":
                    break
        mapid = self.m.r32(dto + DTO_MAP_HEADER)
        if not mons:
            return {"kind": kind, "dex": 0, "shiny": False, "safari": False, "contest": False, "level": 0,
                    "battle_type": bt, "pid": 0, "key": None, "map": mapid, "others": [], "keys": [], "dexes": []}
        keys, dexes = [], []
        for m in mons:
            pid = struct.unpack_from("<I", m, 0)[0]
            tid, sid = struct.unpack_from("<HH", m, 0x08 + 4)
            keys.append("%08x%04x%04x" % (pid, tid, sid))
            dexes.append(struct.unpack_from("<H", m, 0x08)[0])
        first = mons[0]
        pid = struct.unpack_from("<I", first, 0)[0]
        tid, sid = struct.unpack_from("<HH", first, 0x08 + 4)
        return {"kind": kind, "dex": dexes[0], "shiny": _gen4_shiny(pid, tid, sid),
                "safari": bool(bt & BATTLE_TYPE_SAFARI), "contest": False, "level": first[0x8C], "battle_type": bt,
                "pid": pid, "key": keys[0], "map": mapid, "others": dexes[1:], "keys": keys, "dexes": dexes}

    def _battle_ctx(self):
        app = self._battle_app()
        if app is None:
            return None, None
        bs = self.m.r32(app + 0x1C)
        if not _ptr(bs):
            return None, None
        ctx = self.m.r32(bs + BS_CTX)
        return (bs, ctx) if _ptr(ctx) else (bs, None)

    def battle_party_slot(self):
        """(save-party index of the player's active mon, its current battle HP) or None.

        None outside battles, before the first send-out (the BattleContext is zeroed when it is created), in
        battles where the player's party isn't at stake or no mon of the player is out (link, Frontier,
        catching tutorial, Pal Park, Great Marsh) and in the instant of a switch where the battler slot doesn't
        hold a mon of the save party yet. The battle works on a copy of the party; the index returned is the
        position in the SAVE party (matched by PID) - the order party_checked()/the referee's party() use.
        Doubles (two of the player's mons out, or a partner): battlers 0 and 2; the partner's mons never match
        the save party. A player mon at HP 0 is preferred, so a faint of the second mon is reported too."""
        bs, ctx = self._battle_ctx()
        if ctx is None:
            return None
        bt = self.m.r32(bs + BS_BATTLE_TYPE)
        if bt & NO_PLAYER_MON_TYPES:
            return None
        pids = self._save_party_pids()
        if not pids:
            return None
        found = []
        for battler in (0, 2):
            bm = ctx + CTX_BATTLE_MONS + battler * BATTLE_MON_SIZE
            pid = self.m.r32(bm + BM_PID)
            species = self.m.r16(bm + BM_SPECIES)
            max_hp = self.m.r32(bm + BM_MAX_HP)
            if not species or not max_hp or pid not in pids:
                continue
            slot = self.m.r8(ctx + CTX_SELECTED_SLOT + battler)
            bparty = self.m.r32(bs + BS_PARTIES + 4 * battler)
            # the battle party's mon in that slot is this battler? (PID = first word, never encrypted; the record
            # itself may be decrypted in place right now)
            if not _ptr(bparty) or not 0 <= slot < min(self.m.r32(bparty + 4), 6) \
                    or self.m.r32(bparty + 8 + slot * PARTY_MON_SIZE) != pid:
                continue
            hp = self.m.r32(bm + BM_CUR_HP)
            found.append((pids.index(pid), hp if hp < 0x80000000 else 0))
        if not found:
            return None
        fainted = [f for f in found if f[1] == 0]
        return fainted[0] if fainted else found[0]

    def _save_party_pids(self) -> list:
        body = self.save_body()
        if not body:
            return []
        return [self.m.r32(body + PAGE_PARTY + 8 + i * PARTY_MON_SIZE) for i in range(self.party_count())]

    # Soul Link: bag / Hall of Fame / flags ------------------------------------------------------------------
    def balls(self) -> list:
        """[(item id, quantity)] in the bag's Poké Balls pocket (non-empty slots)."""
        body = self.save_body()
        if not body:
            return []
        raw = self.m.read(body + PAGE_BAG + BAG_BALLS, 4 * BALL_POCKET_SIZE)
        out = []
        for i in range(BALL_POCKET_SIZE):
            item, qty = struct.unpack_from("<HH", raw, 4 * i)
            if item:
                out.append((item, qty))
        return out

    def has_poke_balls(self) -> bool:
        """Any ball (items 1-16, Master .. Cherish Ball) with quantity > 0 in the Poké Balls pocket -- or a Great
        Marsh safari game is running (Safari Balls are a counter there, not a bag item)."""
        return any(BALL_FIRST <= item <= BALL_LAST and qty > 0 for item, qty in self.balls()) or \
            self.flag(FLAG_SAFARI_GAME_ACTIVE)

    def flag(self, n: int) -> bool:
        """VarsFlags flag n (pret generated/vars_flags.txt numbering)."""
        body = self.save_body()
        if not body or not 0 <= n < NUM_FLAGS:
            return False
        return bool(self.m.r8(body + PAGE_VARS_FLAGS + VF_FLAGS + n // 8) >> (n % 8) & 1)

    def var(self, n: int) -> int:
        """VarsFlags var n (16384 = VARS_START ..)."""
        body = self.save_body()
        if not body or not VARS_START <= n < VARS_END:
            return 0
        return self.m.r16(body + PAGE_VARS_FLAGS + 2 * (n - VARS_START))

    def in_safari_game(self) -> bool:
        return self.flag(FLAG_SAFARI_GAME_ACTIVE)

    def has_partner(self) -> bool:
        """Walking with a partner (Cheryl, Mira, Riley, Marley, Buck): wild battles are doubles."""
        return self.flag(FLAG_HAS_PARTNER)

    def in_hall_of_fame(self) -> bool:
        """Standing in the Hall of Fame room (map 187)."""
        return self.map_id() == HALL_OF_FAME_MAP

    def hall_of_fame_count(self) -> int:
        """GameRecords RECORD_TIMES_ENTERED_HALL_OF_FAME (+1 by ClearGame each time; saved). The records are
        stored XOR-encrypted (GameRecords_IncrementRecordValue decodes/re-encodes around every change)."""
        body = self.save_body()
        if not body:
            return 0
        raw = self.m.read(body + PAGE_GAME_RECORDS, GAME_RECORDS_SIZE)
        return game_record(raw, RECORD_TIMES_ENTERED_HALL_OF_FAME)

    def entered_hall_of_fame(self) -> bool:
        """Persistent (saved) proof the Champion was beaten and the Hall of Fame entered: FLAG_GAME_COMPLETED or
        TrainerInfo.isMainStoryCleared (both set by ClearGame, the Hall of Fame script's routine) or the HoF
        record count."""
        if self.flag(FLAG_GAME_COMPLETED):
            return True
        if self._trainer(0x1D, 1)[0] & 1:
            return True
        return self.hall_of_fame_count() > 0

    # Soul Link: "the game isn't working on it" across frames -----------------------------------------------
    def _frame(self):
        """The emulator's frame counter (NDSCore.frame_count / ds.DS.frame) or None (fake buses in tests)."""
        f = getattr(self.m, "frame_count", None)
        return f if f is not None else getattr(self.m, "frame", None)

    def _move_snapshot(self):
        """(party records, current box id, current box raw slots) when the party reads clean, else None."""
        party = self.party_checked()
        if party is None:
            return None
        box = self.current_box()
        return tuple(party), box, tuple(self.box_raw(box)) if 0 <= box < NUM_BOXES else ()

    def observe_stable(self) -> bool:
        """Call every frame (or every few frames) while the player is safe - the referee's view should do it in
        its safe() check. Records what the party and the current box look like and returns True when they read
        clean (party_checked) and byte-identical to the last clean observation made on an EARLIER emulated frame.

        Why: the game decrypts records in place in the middle of its frame work; party_checked()'s "two reads
        agree" happens within one paused frame, so it can't see an operation that is still going on. The two
        clean reads need not be on adjacent frames: with a 6-mon party the game works on the lead on about every
        other frame, so adjacent clean frames may never come. The forced moves (move_party_to_box /
        move_box_to_party) only write when the state they are about to change passes this check - against an
        observation by this method or by an earlier attempt of the move itself."""
        snap = self._move_snapshot()
        if snap is None:
            return False
        f = self._frame()
        last = self._obs
        if last is not None and last[1] == snap:
            return f is None or last[0] != f
        self._obs = (f, snap)
        return False

    def reset_stable(self):
        """Forget observe_stable()'s last look (e.g. after loading a save state)."""
        self._obs = None

    def _stable_for_move(self) -> bool:
        """True if the current party + current box equal an observation from an earlier frame. Otherwise the
        current state is recorded and the caller returns False ("try again on a later frame")."""
        return self.observe_stable()

    # Soul Link: current box + forced moves ------------------------------------------------------------------
    def _box_slots(self, box: int) -> list:
        """[(slot, normalised 136-byte record)] of the non-empty slots of a box (records the game is working on
        right now are returned as it will write them back; unreadable 'bad' slots are skipped)."""
        from .platinum_pc import normalise
        out = []
        for i, rec in enumerate(self.box_raw(box)):
            kind, r = normalise(rec)
            if kind == "mon":
                out.append((i, r))
        return out

    def current_box_records(self) -> list:
        """Mons of the current box (PCBoxes.currentBoxID: the box the PC opens on, where the game stores a catch
        when the party is full) as encrypted 136-byte records, in slot order, empty slots left out. Index i of
        this list is what move_box_to_party(i) takes."""
        if not self.in_game():
            return []
        return [r for _slot, r in self._box_slots(self.current_box())]

    def move_party_to_box(self, slot: int) -> bool:
        """Deposit a party mon like the box UI's STORE does (PCBoxes_TryStoreBoxMonInBox + Party_Remove...):
        box part with PP restored (BoxPokemon_RestorePP) and Shaymin back in Land Form, into the first empty
        slot of the current box; if that box is full, into the first box with space after it, which then
        becomes the current box (what the battle does with a catch when the party is full). The party shifts up
        and the last slot is cleared (Pokemon_Init). Call only while is_safe().

        False (nothing changed) if the slot doesn't exist, the party isn't readable right now, no OTHER non-egg
        party mon has HP > 0, the mon holds Mail or has a Ball Capsule (the game refuses both) or all 18
        boxes are full - and also (try again on a later frame) when the party/current box weren't observed
        identical on an earlier frame (observe_stable(); the first attempt records the state)."""
        if self.rom is None:
            raise RuntimeError("move_party_to_box needs PlatinumMemory(bus, rom) (move PP)")
        if not self._stable_for_move():
            return False
        party = self.party_checked()
        if party is None or not 0 <= slot < len(party) or len(party) < 2:
            return False
        plains = [pk4.decrypt(r) for r in party]
        others_ok = any(not _is_egg_plain(p) and struct.unpack_from("<H", p, 0x8E)[0] > 0
                        for i, p in enumerate(plains) if i != slot)
        mon = pk4.PK4(plains[slot])
        if not others_ok or MAIL_FIRST <= mon.item <= MAIL_LAST or mon.d[0x8D]:
            return False
        target = self._free_box_slot()
        if target is None:
            return False
        box, pos = target
        box_pk = pk4.PK4(bytes(mon.d[:BOX_MON_SIZE]))
        box_pk.pp = [(self.rom.move_pp(m) + self.rom.move_pp(m) * min(u, 3) // 5) if m else p
                     for m, u, p in zip(box_pk.moves, box_pk.pp_ups, box_pk.pp)]
        if box_pk.species == SPECIES_SHAYMIN and box_pk.form:
            box_pk.form = 0
        rec = pk4.encrypt(box_pk.plain())
        if box != self.current_box():
            self.m.w32(self.pc_boxes_addr(), box)
        self.write_box_slot(box, pos, rec)
        self._remove_party(slot, party)
        return True

    def _free_box_slot(self):
        """(box, slot) of the first empty slot in the current box, else in the next boxes (wrapping), or None."""
        cur = self.current_box()
        if not 0 <= cur < NUM_BOXES:
            return None
        for k in range(NUM_BOXES):
            box = (cur + k) % NUM_BOXES
            for i, rec in enumerate(self.box_raw(box)):
                if rec == pk4.EMPTY_BOX or (pk4.valid(rec) and pk4.is_empty(rec)):
                    return box, i
        return None

    def _remove_party(self, slot: int, party: list):
        """Party_RemovePokemonBySlotIndex: shift the following mons up, Pokemon_Init the last slot, count - 1."""
        body = self.save_body()
        n = len(party)
        base = body + PAGE_PARTY + 8
        for i in range(slot, n - 1):
            self.m.write(base + i * PARTY_MON_SIZE, bytes(party[i + 1]))
        self.m.write(base + (n - 1) * PARTY_MON_SIZE, pk4.EMPTY_PARTY)
        self.m.w32(body + PAGE_PARTY + 4, n - 1)

    def move_box_to_party(self, box_index: int) -> bool:
        """Withdraw entry `box_index` of current_box_records() into the party like the box UI does
        (Pokemon_FromBoxPokemon = pk4.to_party: level from exp, stats recomputed, HP = max, no status, empty
        mail/capsule; Party_AddPokemon) and clear its box slot (BoxPokemon_Init). False if the party is full or
        not readable right now, or the index doesn't exist - and (try again later) while the state isn't
        stable across frames (see observe_stable). Call only while is_safe()."""
        if self.rom is None:
            raise RuntimeError("move_box_to_party needs PlatinumMemory(bus, rom) (stats, growth)")
        if not self._stable_for_move():
            return False
        party = self.party_checked()
        if party is None or len(party) >= 6:
            return False
        box = self.current_box()
        slots = self._box_slots(box) if 0 <= box < NUM_BOXES else []
        if not 0 <= box_index < len(slots):
            return False
        pos, rec = slots[box_index]
        body = self.save_body()
        n = len(party)
        self.m.write(body + PAGE_PARTY + 8 + n * PARTY_MON_SIZE, pk4.to_party(rec, self.rom))
        self.m.w32(body + PAGE_PARTY + 4, n + 1)
        self.write_box_slot(box, pos, pk4.EMPTY_BOX)
        return True

    def daycare_records(self) -> list | None:
        """Mons left at the Solaceon Pokémon Day Care as encrypted 136-byte box records: [mon 1, mon 2], each only
        if its slot isn't empty (Daycare = 2 x DaycareMon {BoxPokemon, DaycareMail, steps} at body+0x1654).
        None (not []) if a slot can't be read right now (mid-decryption, or corrupt): "unknown", skip the scan."""
        from .platinum_pc import normalise
        body = self.save_body()
        if not body:
            return []
        out = []
        for i in range(2):
            kind, rec = normalise(self.m.read(body + PAGE_DAYCARE + i * DAYCARE_MON_SIZE, BOX_MON_SIZE))
            if kind == "bad":
                return None                             # unreadable right now (or a Bad Egg): don't guess
            if kind == "mon":
                out.append(rec)
        return out


# ---------------------------------------------------------------------------------------------------------
# Soul Link module helpers
# ---------------------------------------------------------------------------------------------------------
def battle_kind_of(bt: int) -> str:
    """'wild' | 'trainer' | 'special' for a FieldBattleDTO.battleType (see PlatinumMemory.battle_kind)."""
    if bt & SPECIAL_BATTLE_TYPES:
        return "special"
    if bt & BATTLE_TYPE_TRAINER:
        return "special" if bt & BATTLE_TYPE_AI else "trainer"
    return "wild"


def _plain_mon(raw: bytes):
    """Plain (A,B,C,D) bytes of an encrypted or in-place-decrypted box/party mon, or None if unreadable."""
    try:
        p = pk4._plain_of(raw)
    except ValueError:
        return None
    if pk4.checksum(p) != struct.unpack_from("<H", p, 6)[0]:
        return None
    return p


def _gen4_shiny(pid: int, tid: int, sid: int) -> bool:
    return (tid ^ sid ^ (pid >> 16) ^ (pid & 0xFFFF)) < 8


def _is_egg_plain(p: bytes) -> bool:
    return pk4.PK4(p).is_egg


def mon_key(record: bytes) -> str:
    """Identity of a Platinum mon (box/party/day-care record, encrypted, plain or decrypted in place):
    PID + TID + SID as 16 hex digits (pk4.mon_key). None of them change on level-up, evolution, PC moves,
    Day Care, nicknaming or hatching (an egg keeps its PID; the hatcher's IDs are already in it)."""
    return pk4.mon_key(record)


def mon_info(record: bytes) -> dict | None:
    """What the Soul Link referee needs of one record (box 136 / party 236 bytes, any of the game's states):
    {key, dex, hp (party records; -1 for box records), egg, nick, level (party records; else 0)} or None if
    the record is empty or unreadable. (Platinum extra; the app builds MonInfo from it.)"""
    p = _plain_mon(bytes(record))
    if p is None:
        return None
    pk = pk4.PK4(p)
    if pk.species == 0:
        return None
    party = len(p) == PARTY_MON_SIZE
    return {"key": "%08x%04x%04x" % (pk.pid, pk.tid, pk.sid), "dex": pk.species, "hp": pk.hp if party else -1,
            "egg": pk.is_egg, "nick": pk.nickname, "level": pk.party_level if party else 0}


def game_record(raw: bytes, rec_id: int) -> int:
    """Value of one GameRecords record from the raw (encrypted) GameRecords bytes (pret game_records.c:
    records 1.. are XORed with the LCRNG stream seeded by byteSum | modifier << 16; record 0 = steps, plain).
    0 if the decrypted bytes don't add up to byteSum (being updated right now, or no records at all)."""
    raw = bytes(raw)
    if len(raw) < GAME_RECORDS_SIZE:
        return 0
    n32, n16 = 71, 77
    byte_sum, modifier = struct.unpack_from("<HH", raw, 4 * n32 + 2 * n16 + 2)
    if rec_id == 0:
        return struct.unpack_from("<I", raw, 0)[0]
    dec = raw[:4] + pk4._crypt(raw[4:4 * n32 + 2 * n16 + 2], byte_sum | modifier << 16)
    if sum(dec[4:]) & 0xFFFF != byte_sum:          # EncodeGameRecords stores the plain byte sum: else mid-update
        return 0
    if rec_id < n32:
        return struct.unpack_from("<I", dec, 4 * rec_id)[0]
    if rec_id < n32 + n16:
        return struct.unpack_from("<H", dec, 4 * n32 + 2 * (rec_id - n32))[0]
    return 0


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


def location_of(map_id: int) -> str:
    """The map's location label key (e.g. 'jubilife_tv'; what the game shows when you enter)."""
    if 0 <= map_id < len(MAP_LOCATION):
        return LOCATIONS[MAP_LOCATION[map_id]][0]
    return "map_%04x" % map_id


def area_of(map_id: int) -> str:
    """Soul Link area key of a map (client/yellowlink/platinum_areas.py, generated by
    dev/platinum/gen_soullink_areas.py): the location label, with buildings counted in the town/route they stand
    in (Jubilife TV -> 'jubilife_city', Pokémon Day Care -> 'solaceon_town'), the Lost Tower separate from
    Route 209 and unused/link maps one area each. Every dungeon floor shares its place's area (Mt. Coronet,
    Eterna Forest, Iron Island...). Unknown ids -> 'map_XXXX'."""
    from .platinum_areas import AREAS
    if 0 <= map_id < len(AREAS):
        return AREAS[map_id]
    return "map_%04x" % map_id


def area_name(area: str) -> str:
    """Readable name of an area key (the ROM's location name where there is one); a Soul Link shiny-clause key
    'route_201*2' is named like its area."""
    from .platinum_areas import AREA_NAMES
    area = area.split("*")[0]
    return AREA_NAMES.get(area) or LOCATION_NAMES.get(area) or area.replace("_", " ").title()


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
