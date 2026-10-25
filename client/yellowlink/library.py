"""The player's game library: which ROMs they've added, per-game saves and resume snapshots."""
from __future__ import annotations

import hashlib
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("yellowlink.library")


@dataclass(frozen=True)
class GameInfo:
    id: str
    title: str
    region: str          # "kanto" | "johto"
    full_support: bool   # multiplayer features (friends, trades, battles, wide view) verified


# Supported games (English releases; SHA1s from the pret disassembly projects).
# Only Yellow (Kanto) and Crystal (Johto) are supported — one game per region keeps things simple.
KNOWN_GAMES = {
    "cc7d03262ebfaf2f06772c1a480c7d9d5f4a38e1": GameInfo("yellow", "Pokémon Yellow", "kanto", True),
    "f4cd194bdee0d04ca4eac29e09b8e4e9d818c133": GameInfo("crystal", "Pokémon Crystal", "johto", True),   # v1.0
    "f2f52230b536214ef7c9924f483392993e226cfb": GameInfo("crystal", "Pokémon Crystal", "johto", False),  # v1.1: unverified
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
]


def sha1_file(path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def identify(path) -> GameInfo | None:
    try:
        return KNOWN_GAMES.get(sha1_file(path))
    except OSError:
        return None


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
        # only supported games (0.4.x also accepted Red/Blue/Gold/Silver; those are simply ignored now)
        return {k: v for k, v in self.cfg.get("games", {}).items() if k in GAMES_BY_ID and Path(v).exists()}

    @property
    def current(self) -> str | None:
        cur = self.cfg.get("current_game")
        if cur in self.games:
            return cur
        return next(iter(self.games), None)

    def info(self, game_id: str) -> GameInfo:
        return GAMES_BY_ID.get(game_id) or GameInfo(game_id, game_id, "unknown", False)

    def rom_path(self, game_id: str | None = None) -> str | None:
        return self.games.get(game_id or self.current)

    def save_path(self, game_id: str | None = None) -> Path:
        return self.dir / "saves" / f"{game_id or self.current}.sav"

    def resume_path(self, game_id: str | None = None) -> Path:
        return self.dir / "resume" / f"{game_id or self.current}.state"

    def add_rom(self, path: str) -> GameInfo:
        """Identify a ROM and keep a private copy. Raises ValueError for unknown files."""
        info = identify(path)
        if info is None:
            raise ValueError("That file isn't English Pokémon Yellow or Crystal (the two supported games).")
        dest = self.dir / "roms" / f"{info.id}.gb{'c' if info.region == 'johto' else ''}"
        if Path(path).resolve() != dest.resolve():
            shutil.copy2(path, dest)
        self.cfg.setdefault("games", {})[info.id] = str(dest)
        self.cfg.save()
        log.info("added %s from %s", info.id, path)
        return info

    def set_current(self, game_id: str):
        self.cfg["current_game"] = game_id
        if game_id in self.games:
            self.cfg["rom_path"] = self.games[game_id]
        self.cfg.save()
