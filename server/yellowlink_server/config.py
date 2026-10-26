"""Server configuration: defaults <- config.json <- environment <- CLI args."""

from __future__ import annotations

import json
import logging
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
    # Sinnoh game (SPEC.md section 10): "any" = players choose Platinum or Definitive Platinum, "definitive" = the
    # server asks everyone to play Definitive Platinum (made from their own Platinum with the server's patch files)
    sinnoh: str = "any"


SINNOH_MODES = ("any", "definitive")


def load_config(path: str | None = None, *, host: str | None = None,
                port: int | None = None, env: dict | None = None) -> Config:
    """Build a Config.

    Precedence (lowest to highest): built-in defaults, the JSON file at
    ``path``, environment variables (YL_KEY, YL_PORT, YL_DATA_DIR, YL_SERVER_NAME, YL_MOTD,
    YL_MOVED_TO, YL_MOVED_KEY, YL_SINNOH), and
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
    if env.get("YL_SINNOH"):
        cfg.sinnoh = env["YL_SINNOH"]
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
    sinnoh = str(cfg.sinnoh or "any").strip().lower()
    if sinnoh not in SINNOH_MODES:
        logging.getLogger("yellowlink").warning("YL_SINNOH=%r is not one of %s: using 'any'", cfg.sinnoh,
                                                "/".join(SINNOH_MODES))
        sinnoh = "any"
    cfg.sinnoh = sinnoh
    return cfg
