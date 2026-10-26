"""The player's game library: which ROMs they've added, per-game saves and resume snapshots."""
from __future__ import annotations

import hashlib
import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("yellowlink.library")


@dataclass(frozen=True)
class GameInfo:
    id: str
    title: str
    region: str          # "kanto" | "johto" | "sinnoh"
    full_support: bool   # multiplayer features (friends, trades, battles, wide view) verified
    platform: str = "gb"  # "gb" | "gbc" | "nds" (which emulator core runs it, ROM file extension)
    variant: str = ""     # Platinum: "vanilla" | "definitive" | "rev1" (SPEC.md section 10)

    @property
    def ext(self) -> str:
        return "." + self.platform


# Supported games (English releases; SHA1s from the pret disassembly projects).
# Yellow (Kanto), Crystal (Johto) and Platinum (Sinnoh, Nintendo DS) — one game per region keeps things simple.
PLATINUM_SHA1 = "ce81046eda7d232513069519cb2085349896dec7"     # Pokémon Platinum (USA, rev 0)
# Definitive Platinum by BigNation = rev 0 or rev 1 + BigNation's patch from the player's server (SPEC.md section 10)
DEFPLAT_SHA1 = "2f368f0f68f98058699a8dd0b1f9af3c970172a2"
PLATINUM_VARIANTS = {"vanilla": PLATINUM_SHA1, "definitive": DEFPLAT_SHA1}     # the playable Sinnoh games
VARIANT_TITLES = {"vanilla": "Pokémon Platinum", "definitive": "Definitive Platinum"}
VARIANT_FILES = {"vanilla": "platinum.nds", "definitive": "platinum-definitive.nds", "rev1": "platinum-rev1.nds"}
DEFPLAT_INFO = GameInfo("platinum", "Definitive Platinum", "sinnoh", True, "nds", "definitive")
REV1_INFO = GameInfo("platinum", "Pokémon Platinum (Rev 1)", "sinnoh", False, "nds", "rev1")
KNOWN_GAMES = {
    "cc7d03262ebfaf2f06772c1a480c7d9d5f4a38e1": GameInfo("yellow", "Pokémon Yellow", "kanto", True),
    "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133": GameInfo("crystal", "Pokémon Crystal", "johto", True, "gbc"),  # v1.0
    "f2f52230b536214ef7c9924f483392993e226cfb": GameInfo("crystal", "Pokémon Crystal", "johto", False, "gbc"),  # v1.1
    PLATINUM_SHA1: GameInfo("platinum", "Pokémon Platinum", "sinnoh", True, "nds", "vanilla"),
    DEFPLAT_SHA1: DEFPLAT_INFO,
}
CRYSTAL_REVISIONS = {
    "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133": "1.0",
    "f2f52230b536214ef7c9924f483392993e226cfb": "1.1",
}
GAMES_BY_ID = {}
for _g in KNOWN_GAMES.values():          # first entry per id wins (Crystal v1.0 over v1.1)
    GAMES_BY_ID.setdefault(_g.id, _g)
REGIONS = [
    ("kanto", "Kanto", ["yellow"]),
    ("johto", "Johto", ["crystal"]),
    ("sinnoh", "Sinnoh", ["platinum"]),
]
GAME_FILE_TYPES = [("Pokémon game file", "*.gb *.gbc *.nds"), ("All files", "*.*")]


def region_name(game_id) -> str:
    """'Kanto' / 'Johto' / 'Sinnoh' for a game id, '' if unknown (e.g. still on the title screen)."""
    info = GAMES_BY_ID.get(game_id or "")
    return next((name for rid, name, _ in REGIONS if info and rid == info.region), "")


_SHA_CACHE: dict = {}


def sha1_file(path) -> str:
    """SHA1 of a file (cached per path/size/mtime: a DS ROM is 128 MB)."""
    st = os.stat(path)
    key = (os.path.abspath(path), st.st_size, st.st_mtime_ns)
    if key not in _SHA_CACHE:
        h = hashlib.sha1()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        _SHA_CACHE[key] = h.hexdigest()
    return _SHA_CACHE[key]


def is_platinum_rev1(path) -> bool:
    """Pokémon Platinum (USA) rev 1, by its header: title POKEMON PL, game code CPUE, ROM version (0x1E) 1. Not a
    proof (we don't know its SHA1): patching it proves it (Adler-32 per window + the result's SHA1)."""
    try:
        with open(path, "rb") as f:
            head = f.read(0x20)
    except OSError:
        return False
    return len(head) == 0x20 and head[:10] == b"POKEMON PL" and head[0x0C:0x10] == b"CPUE" and head[0x1E] == 1


def identify(path) -> GameInfo | None:
    try:
        info = KNOWN_GAMES.get(sha1_file(path))
    except OSError:
        return None
    if info is None and is_platinum_rev1(path):
        return REV1_INFO
    return info


def variant_full(variant: str) -> bool:
    """Whether YellowLink's Platinum adapter is verified on this variant (multiplayer, Soul Link...): the adapter's
    SUPPORTED_SHA1S (owned by platinum.py). Definitive runs single-player until it is listed there."""
    sha = PLATINUM_VARIANTS.get(variant)
    if not sha:
        return False
    try:
        from . import platinum
    except Exception:  # noqa: BLE001 - adapter missing/broken
        return False
    return sha in (getattr(platinum, "SUPPORTED_SHA1S", None) or (getattr(platinum, "PLATINUM_SHA1", None),))


class Library:
    """Lives inside Config: cfg['games'] = {game_id: rom_path}, cfg['current_game'] = id."""

    def __init__(self, cfg, base_dir: Path):
        self.cfg = cfg
        self.dir = base_dir
        (self.dir / "roms").mkdir(parents=True, exist_ok=True)
        (self.dir / "saves").mkdir(parents=True, exist_ok=True)
        (self.dir / "resume").mkdir(parents=True, exist_ok=True)
        self._migrate()

    def _migrate(self):
        """Older versions had a single rom_path (always Yellow)."""
        games = self.cfg.setdefault("games", {})
        old = self.cfg.get("rom_path")
        if old and not games and Path(old).exists():
            info = identify(old)
            games[info.id if info else "yellow"] = old
            self.cfg.setdefault("current_game", info.id if info else "yellow")
            self.cfg.save()

    @property
    def games(self) -> dict:
        # only supported games (0.4.x also accepted Red/Blue/Gold/Silver; those are simply ignored now).
        # Platinum: the file of the variant that is played (vanilla or Definitive; a rev 1 alone isn't playable)
        out = {k: v for k, v in self.cfg.get("games", {}).items()
               if k in GAMES_BY_ID and k != "platinum" and Path(v).exists()}
        v = self.variant("platinum")
        if v:
            out["platinum"] = self.variant_path("platinum", v)
        order = list(self.cfg.get("games", {}))
        return dict(sorted(out.items(), key=lambda kv: order.index(kv[0]) if kv[0] in order else len(order)))

    # --- variants (Platinum: original or Definitive; SPEC.md section 10) ---------------------------------------
    def variant_path(self, game_id: str, variant: str) -> str | None:
        """The file of one variant, if it's there."""
        if game_id != "platinum":
            return None
        if variant == "vanilla":
            p = self.cfg.get("games", {}).get("platinum")
        else:
            p = (self.cfg.get("variants", {}).get("platinum") or {}).get(variant)
        return p if p and Path(p).exists() else None

    def variants(self, game_id: str = "platinum") -> list:
        """The playable variants the player has, e.g. ["vanilla", "definitive"]."""
        if game_id != "platinum":
            return []
        return [v for v in PLATINUM_VARIANTS if self.variant_path(game_id, v)]

    def server_sinnoh(self) -> str:
        """What the current server last said about Sinnoh ("any" | "definitive"; welcome.sinnoh)."""
        srv = getattr(self.cfg, "active_server", None)
        try:
            return (srv or {}).get("sinnoh") or "any"
        except AttributeError:
            return "any"

    def variant(self, game_id: str = "platinum") -> str | None:
        """The variant that is played: a run's own; else Definitive if the server requires it and the player has
        it; else the player's pick; else whichever is there (original first). None = no playable Platinum."""
        if game_id != "platinum":
            return None
        chal = getattr(self.cfg, "challenge", None)
        if isinstance(chal, dict) and chal.get("game") == game_id:
            return chal.get("variant") or "vanilla"
        have = self.variants(game_id)
        if not have:
            return None
        if self.server_sinnoh() == "definitive" and "definitive" in have:
            return "definitive"
        pick = (self.cfg.get("variant") or {}).get(game_id)
        return pick if pick in have else have[0]

    def pick_variant(self, game_id: str, variant: str):
        self.cfg.setdefault("variant", {})[game_id] = variant
        self.cfg.save()

    def patch_source(self) -> tuple | None:
        """(rev, path) of the Platinum to make Definitive from: rev 0 (supported) first, else a rev 1."""
        p = self.variant_path("platinum", "vanilla")
        if p:
            return "rev0", p
        p = (self.cfg.get("variants", {}).get("platinum") or {}).get("rev1")
        if p and Path(p).exists():
            return "rev1", p
        return None

    def definitive_dest(self) -> Path:
        return self.dir / "roms" / VARIANT_FILES["definitive"]

    def set_variant_file(self, game_id: str, variant: str, path):
        if variant == "vanilla":
            self.cfg.setdefault("games", {})[game_id] = str(path)
        else:
            self.cfg.setdefault("variants", {}).setdefault(game_id, {})[variant] = str(path)
        self.cfg.save()

    @property
    def current(self) -> str | None:
        cur = self.cfg.get("current_game")
        if cur in self.games:
            return cur
        return next(iter(self.games), None)

    def info(self, game_id: str, variant: str | None = None) -> GameInfo:
        if game_id == "platinum" and variant == "definitive":
            return DEFPLAT_INFO
        return GAMES_BY_ID.get(game_id) or GameInfo(game_id, game_id, "unknown", False)

    def rom_path(self, game_id: str | None = None, variant: str | None = None) -> str | None:
        if variant:
            return self.variant_path(game_id or self.current, variant)
        return self.games.get(game_id or self.current)

    def save_path(self, game_id: str | None = None) -> Path:
        return self.dir / "saves" / f"{game_id or self.current}.sav"

    def resume_path(self, game_id: str | None = None) -> Path:
        return self.dir / "resume" / f"{game_id or self.current}.state"

    def add_rom(self, path: str) -> GameInfo:
        """Identify a ROM and keep a private copy. Raises ValueError for unknown files."""
        info = identify(path)
        if info is None:
            raise ValueError("That file isn't English Pokémon Yellow, Crystal or Platinum (the supported games).")
        if info.variant:
            dest = self.dir / "roms" / VARIANT_FILES[info.variant]
        else:
            dest = self.dir / "roms" / f"{info.id}{info.ext}"
        if Path(path).resolve() != dest.resolve():
            shutil.copy2(path, dest)
        if info.variant:
            self.set_variant_file(info.id, info.variant, dest)
        else:
            self.cfg.setdefault("games", {})[info.id] = str(dest)
            self.cfg.save()
        log.info("added %s from %s", info.id, path)
        return info

    def set_current(self, game_id: str):
        self.cfg["current_game"] = game_id
        if game_id in self.games:
            self.cfg["rom_path"] = self.games[game_id]
        self.cfg.save()
