"""Dev/test shortcut: start the game anywhere, with any story progress, through the game's own CONTINUE.

Platinum re-reads the battery when CONTINUE is picked (game_start.c TryLoadingSave), so RAM edits on the title
screen are lost. Instead: edit the save image in RAM (flags, vars, bag, Pokédex, party ... = test shortcuts), write
it into the battery as a valid save (the general block's CRC16-CCITT footer, same counters as the box block so the
loader accepts it), reset, CONTINUE. To choose where the player appears we use the game's own "came back from the
Communication Club" path (field_map_change.c FieldTask_LoadSavedGameMap): FLAG_COMMUNICATION_CLUB_ACCESSIBLE (2406)
set -> the map loads at FieldOverworldState.special (a Location {map, warpId -1, x, z, dir}) with fresh map objects
and its scripts (OnTransition etc.) run as on any arrival.

    w = DevWarp(d)                    # d = dev/platinum/ds.DS
    w.boot("pt_title.dsst")           # any state whose battery holds a save (pt_title: LUCAS, Twinleaf)
    w.flag(2404); w.national_dex(); w.var(0x4043, 0x1209); w.key_item(454)
    w.go(342, 140, 850)               # -> 'loaded' in Route 201 at (140, 850)
"""
import os
import struct
import tempfile

from yellowlink import platinum as P
from yellowlink import sinnoh_events as E

GENERAL_SIZE = 0xCF2C                 # general block incl. 0x14-byte footer (SaveBlockFooter)
FOOTER = GENERAL_SIZE - 0x14
SECTOR = 0x40000
SAVE_SIZE = 0x80000
FLAG_COMMUNICATION_CLUB_ACCESSIBLE = 2406
SPECIAL_LOCATION = P.PAGE_FIELD_PLAYER + 0x3C      # FieldOverworldState: player, entrance, previous, special, exit


def crc16(data: bytes) -> int:
    """MATH_CalcCRC16CCITT (poly 0x1021, init 0xFFFF): checked against the footer of pt_saved.sav."""
    c = 0xFFFF
    for b in data:
        c ^= b << 8
        for _ in range(8):
            c = ((c << 1) ^ 0x1021) & 0xFFFF if c & 0x8000 else (c << 1) & 0xFFFF
    return c


class DevWarp:
    def __init__(self, d, states=None):
        self.d = d
        self.pm = P.PlatinumMemory(d)
        self.states = states or os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "states")
        self.tmp = tempfile.mkdtemp(prefix="yl_devwarp_")

    @property
    def body(self):
        return self.pm.save_body()

    def boot(self, state="pt_title.dsst"):
        self.d.load(os.path.join(self.states, state))
        self.d.run(2)

    # --- save edits (test shortcuts) -----------------------------------------------------------------------
    def flag(self, n, on=True):
        a = self.body + P.PAGE_VARS_FLAGS + P.VF_FLAGS + n // 8
        v = self.d.r8(a)
        self.d.w8(a, (v | (1 << (n % 8))) if on else (v & ~(1 << (n % 8))))

    def var(self, n, value):
        self.d.w16(self.body + P.PAGE_VARS_FLAGS + 2 * (n - P.VARS_START), value)

    def national_dex(self):
        self.d.w8(self.body + E.PAGE_POKEDEX + E.POKEDEX_NATIONAL, 1)

    def key_item(self, item, qty=1):
        base = self.body + E.PAGE_BAG + E.BAG_KEY_ITEMS
        for i in range(E.KEY_ITEM_POCKET_SIZE):
            it, q = struct.unpack("<HH", self.d.read(base + 4 * i, 4))
            if it == item or (it == 0 and q == 0):
                self.d.write(base + 4 * i, struct.pack("<HH", item, qty))
                return
        raise RuntimeError("key items full")

    def register_item(self, item):
        """Bag.registeredItem (the item on the Y button)."""
        self.d.w32(self.body + E.PAGE_BAG + BAG_REGISTERED, item)

    # --- write the battery and continue -------------------------------------------------------------------
    def _battery(self) -> bytes:
        p = os.path.join(self.tmp, "cur.sav")
        self.d.emu.backup.export_file(p)
        with open(p, "rb") as f:
            return f.read()

    def commit(self):
        """RAM save image -> battery (general block of every valid sector, same counters, new CRC)."""
        bat = bytearray(self._battery())
        if len(bat) < SAVE_SIZE:
            raise RuntimeError("battery export failed")
        body = self.d.read(self.body, FOOTER)
        n = 0
        for base in (0, SECTOR):
            foot = bat[base + FOOTER:base + GENERAL_SIZE]
            size, sig = struct.unpack_from("<II", foot, 8)
            if size != GENERAL_SIZE or sig != 0x20060623:
                continue
            bat[base:base + FOOTER] = body
            struct.pack_into("<H", bat, base + FOOTER + 0x12, crc16(body))
            n += 1
        if not n:
            raise RuntimeError("no valid save in the battery")
        p = os.path.join(self.tmp, "new.sav")
        with open(p, "wb") as f:
            f.write(bytes(bat[:SAVE_SIZE]))
        if not self.d.emu.backup.import_file(p, SAVE_SIZE):
            raise RuntimeError("DeSmuME refused the save")

    def go(self, map_id, x, z, facing=1):
        """Continue into map_id at tile (x, z) (outdoor maps: global coordinates). Returns fast_continue's result."""
        self.d.write(self.body + SPECIAL_LOCATION, struct.pack("<5i", map_id, -1, x, z, facing))
        self.flag(FLAG_COMMUNICATION_CLUB_ACCESSIBLE)
        self.commit()
        self.d.emu.reset()
        self.d.run(2)
        r = P.fast_continue(self.d)
        return r


BAG_REGISTERED = (165 + 50 + 100 + 12 + 40 + 64 + 15 + 30) * 4     # Bag.registeredItem (u32) after the 8 pockets
