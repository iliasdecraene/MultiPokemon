"""Server configuration: defaults <- config.json <- environment <- CLI args."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, fields

from . import __version__


@dataclass
class Config:
    host: str = "0.0.0.0"
    port: int = 8765
    key: str = "change-me"
    latest_client: str = __version__  # server and client are released together
    download_url: str = "https://github.com/iliasdecraene/MultiPokemon/releases/latest"
    motd: str = "Welcome!"
    name: str = ""                 # shown to players (sidebar + welcome); e.g. "Vizkes Pokémon"
    data_dir: str = "data"
    # Forwarding (after merging this server into another, SPEC.md section 8): clients >= 0.7.1 are told the new
    # address + password and switch themselves; older clients only get the update notice.
    moved_to: str = ""
    moved_key: str = ""
    # Raids (SPEC.md section 12): on by default; every `raid_every` minutes, open `raid_open` minutes.
    # `raid_force` (testing only): "region", "region:species" or "region:species:shiny" for every raid.
    raids: bool = True
    raid_every: float = 120.0
    raid_open: float = 15.0
    raid_force: str = ""
    raid_first: float = -1.0       # testing only (YL_RAID_FIRST): seconds after start until a first raid; -1 = off
    raid_spots: str = ""           # testing only (YL_RAID_SPOTS): another spot file than the vendored raid_spots.json
    raid_catch: str = ""           # testing only (YL_RAID_CATCH): "always" / "never" instead of the share roll


def load_config(path: str | None = None, *, host: str | None = None,
                port: int | None = None, env: dict | None = None) -> Config:
    """Build a Config.

    Precedence (lowest to highest): built-in defaults, the JSON file at
    ``path``, environment variables (YL_KEY, YL_PORT, YL_DATA_DIR, YL_SERVER_NAME, YL_MOTD,
    YL_MOVED_TO, YL_MOVED_KEY, YL_RAIDS, YL_RAID_EVERY, YL_RAID_OPEN, YL_RAID_FORCE, YL_RAID_FIRST, YL_RAID_SPOTS,
    YL_RAID_CATCH), and
    finally explicit ``host``/``port`` arguments (from the command line).
    """
    env = os.environ if env is None else env
    cfg = Config()
    known = {f.name for f in fields(Config)}
    if path:
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        if not isinstance(raw, dict):
            raise ValueError(f"{path}: top level must be a JSON object")
        for k, v in raw.items():
            if k in known:
                setattr(cfg, k, v)
    if env.get("YL_KEY"):
        cfg.key = env["YL_KEY"]
    if env.get("YL_PORT"):
        cfg.port = int(env["YL_PORT"])
    if env.get("YL_DATA_DIR"):
        cfg.data_dir = env["YL_DATA_DIR"]
    if env.get("YL_SERVER_NAME"):
        cfg.name = env["YL_SERVER_NAME"]
    if env.get("YL_MOTD"):
        cfg.motd = env["YL_MOTD"]
    if env.get("YL_MOVED_TO"):
        cfg.moved_to = env["YL_MOVED_TO"]
    if env.get("YL_MOVED_KEY"):
        cfg.moved_key = env["YL_MOVED_KEY"]
    if env.get("YL_RAIDS"):
        cfg.raids = env["YL_RAIDS"].strip().lower() not in ("off", "0", "false", "no")
    if env.get("YL_RAID_EVERY"):
        cfg.raid_every = float(env["YL_RAID_EVERY"])
    if env.get("YL_RAID_OPEN"):
        cfg.raid_open = float(env["YL_RAID_OPEN"])
    if env.get("YL_RAID_FORCE"):
        cfg.raid_force = env["YL_RAID_FORCE"]
    if env.get("YL_RAID_FIRST"):
        cfg.raid_first = float(env["YL_RAID_FIRST"])
    if env.get("YL_RAID_SPOTS"):
        cfg.raid_spots = env["YL_RAID_SPOTS"]
    if env.get("YL_RAID_CATCH"):
        cfg.raid_catch = env["YL_RAID_CATCH"]
    if host is not None:
        cfg.host = host
    if port is not None:
        cfg.port = port
    cfg.port = int(cfg.port)
    cfg.key = str(cfg.key)
    cfg.name = str(cfg.name or "").strip()[:30]
    cfg.moved_to = str(cfg.moved_to or "").strip()
    if cfg.moved_to and "://" not in cfg.moved_to:
        cfg.moved_to = "ws://" + cfg.moved_to
    cfg.moved_key = str(cfg.moved_key or "")
    if isinstance(cfg.raids, str):
        cfg.raids = cfg.raids.strip().lower() not in ("off", "0", "false", "no")
    cfg.raids = bool(cfg.raids)
    cfg.raid_every = max(1.0, float(cfg.raid_every))
    cfg.raid_open = max(0.5, min(float(cfg.raid_open), cfg.raid_every))
    cfg.raid_force = str(cfg.raid_force or "")
    cfg.raid_first = float(cfg.raid_first)
    return cfg
