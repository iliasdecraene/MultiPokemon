"""Persistent player store (uid -> name, trainer, party, badges, last_seen)."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
import time

log = logging.getLogger("yellowlink.store")


class PlayerStore:
    def __init__(self, data_dir: str, debounce: float = 1.0):
        self.data_dir = data_dir
        self.path = os.path.join(data_dir, "players.json")
        self.debounce = debounce
        self.players: dict[str, dict] = {}
        self._save_task: asyncio.Task | None = None
        self._load()

    # ------------------------------------------------------------------ io
    def _load(self) -> None:
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
        except FileNotFoundError:
            return
        except (OSError, ValueError) as exc:
            log.error("could not read %s (%s); starting with an empty store", self.path, exc)
            return
        players = raw.get("players", raw) if isinstance(raw, dict) else {}
        for uid, p in players.items():
            if isinstance(p, dict):
                self.players[str(uid)] = {
                    "name": str(p.get("name", "?")),
                    "trainer": str(p.get("trainer", "")),
                    "party": list(p.get("party", [])),
                    "badges": int(p.get("badges", 0) or 0),
                    "last_seen": float(p.get("last_seen", 0) or 0),
                }
        log.info("loaded %d players from %s", len(self.players), self.path)

    def save_now(self) -> None:
        """Write players.json atomically (temp file + os.replace)."""
        os.makedirs(self.data_dir, exist_ok=True)
        payload = json.dumps({"version": 1, "players": self.players}, indent=1)
        fd, tmp = tempfile.mkstemp(prefix=".players-", suffix=".tmp", dir=self.data_dir)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def schedule_save(self) -> None:
        """Debounced save: coalesces bursts of changes into one write."""
        if self._save_task is not None and not self._save_task.done():
            return
        self._save_task = asyncio.get_running_loop().create_task(self._delayed_save())

    async def _delayed_save(self) -> None:
        try:
            await asyncio.sleep(self.debounce)
        except asyncio.CancelledError:
            return
        try:
            self.save_now()
        except Exception:
            log.exception("failed to save %s", self.path)

    async def flush(self) -> None:
        if self._save_task is not None and not self._save_task.done():
            self._save_task.cancel()
        try:
            self.save_now()
        except Exception:
            log.exception("failed to save %s", self.path)

    # ------------------------------------------------------------- mutation
    def get(self, uid: str) -> dict | None:
        return self.players.get(uid)

    def touch(self, uid: str, name: str) -> None:
        p = self.players.setdefault(uid, {"name": name, "trainer": "", "party": [],
                                          "badges": 0, "last_seen": 0.0})
        p["name"] = name
        p["last_seen"] = time.time()
        self.schedule_save()

    def set_game(self, uid: str, game: str) -> bool:
        """Remember which game (region) a player is in. Returns True if it changed."""
        p = self.players.get(uid)
        if p is None or p.get("game") == game:
            return False
        p["game"] = game
        self.schedule_save()
        return True

    def set_party(self, uid: str, mons: list, trainer: str, badges: int) -> bool:
        """Store a party snapshot. Returns True if anything changed."""
        p = self.players.get(uid)
        if p is None:
            return False
        changed = (p["party"] != mons or p["trainer"] != trainer or p["badges"] != badges)
        p["party"], p["trainer"], p["badges"] = mons, trainer, badges
        p["last_seen"] = time.time()
        if changed:
            self.schedule_save()
        return changed
