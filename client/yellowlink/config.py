"""Per-user settings and save locations."""
from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path


def app_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        d = base / "YellowLink"
    elif sys.platform == "darwin":
        d = Path.home() / "Library" / "Application Support" / "YellowLink"
    else:
        d = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "yellowlink"
    d.mkdir(parents=True, exist_ok=True)
    (d / "saves").mkdir(exist_ok=True)
    return d


DEFAULTS = {
    "server_url": "ws://YOUR-SERVER-IP:8765",
    "room_key": "",
    "name": "",
    "uid": "",
    "rom_path": "",
    "scale": 4,
    "volume": 60,
    "show_names": True,
}


class Config(dict):
    def __init__(self, path: Path | None = None):
        super().__init__(DEFAULTS)
        self.path = path or (app_dir() / "config.json")
        if self.path.exists():
            try:
                self.update(json.loads(self.path.read_text(encoding="utf-8")))
            except (ValueError, OSError):
                pass
        if not self.get("uid"):
            self["uid"] = str(uuid.uuid4())
            self.save()

    def save(self):
        tmp = self.path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(self, indent=2), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError:
            pass  # non-fatal (file locked by AV/OneDrive); settings stay in memory

    def is_complete(self) -> bool:
        return bool(self.get("name") and self.get("rom_path") and Path(self["rom_path"]).exists()
                    and self.get("server_url") and "YOUR-SERVER" not in self["server_url"])

    @property
    def challenge(self) -> dict | None:
        """{"id", "game"} while playing a challenge (separate save), else None. See SPEC.md section 7."""
        c = self.get("active_challenge")
        return c if isinstance(c, dict) and c.get("id") and c.get("game") else None

    def challenge_dir(self, cid: str) -> Path:
        d = app_dir() / "challenges" / "".join(ch for ch in cid if ch.isalnum() or ch in "-_")[:40]
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def game_id(self) -> str:
        c = self.challenge
        return c["game"] if c else (self.get("current_game") or "yellow")

    @property
    def save_path(self) -> Path:
        c = self.challenge
        if c:
            return self.challenge_dir(c["id"]) / "game.sav"
        return app_dir() / "saves" / f"{self.game_id}.sav"

    @property
    def resume_path(self) -> Path:
        c = self.challenge
        if c:
            return self.challenge_dir(c["id"]) / "resume.state"
        d = app_dir() / "resume"
        d.mkdir(exist_ok=True)
        return d / f"{self.game_id}.state"

    @property
    def backup_dir(self) -> Path:
        d = app_dir() / "saves" / "backups"
        d.mkdir(exist_ok=True)
        return d
