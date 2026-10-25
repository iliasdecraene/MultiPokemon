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
    data_dir: str = "data"


def load_config(path: str | None = None, *, host: str | None = None,
                port: int | None = None, env: dict | None = None) -> Config:
    """Build a Config.

    Precedence (lowest to highest): built-in defaults, the JSON file at
    ``path``, environment variables (YL_KEY, YL_PORT, YL_DATA_DIR), and
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
    if host is not None:
        cfg.host = host
    if port is not None:
        cfg.port = port
    cfg.port = int(cfg.port)
    cfg.key = str(cfg.key)
    return cfg
