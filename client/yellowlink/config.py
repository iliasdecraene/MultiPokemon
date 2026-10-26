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
    "exp_share_all": True,      # modern Exp. Share rule in every game (Settings panel)
}


MAIN_SERVER = "main"      # the first server: keeps the original folders (saves/, resume/, pc/, challenges/)


def normalize_url(url: str) -> str:
    url = "".join((url or "").split()).strip('"')          # no spaces anywhere (pasted addresses)
    scheme, sep, rest = url.partition("://")
    if sep and scheme.lower() in ("ws", "wss"):
        return scheme.lower() + "://" + rest
    if sep and scheme.lower() in ("http", "https"):          # pasted with the wrong prefix
        host = rest.split("/", 1)[0].split(":", 1)[0]
        secure = scheme.lower() == "https" and not host.replace(".", "").isdigit()   # plain IPs have no TLS
        return ("wss" if secure else "ws") + "://" + rest
    return "ws://" + url if url else url


def url_looks_ok(url: str) -> bool:
    """ws://host:port with a sane host (catches typos like spaces, commas, a letter O in an IP, a doubled prefix)."""
    import re
    m = re.fullmatch(r"wss?://([A-Za-z0-9.\-]+)(?::(\d{1,5}))?/?", url or "")
    if not m:
        return False
    host = m.group(1)
    if any(ch.isdigit() for ch in host.rsplit(".", 1)[-1]):   # meant as an IP address: exactly 4 numbers 0-255
        parts = host.split(".")
        return len(parts) == 4 and all(p.isdigit() and int(p) <= 255 for p in parts)
    return "." in host or host == "localhost"


def _url_key(url: str) -> str:
    return normalize_url(url).lower().rstrip("/")


class Config(dict):
    """Settings. Servers (one per group of friends): cfg["servers"] = [{id, label, url, key, uid, game}], the active
    one in cfg["server_id"]. The active server's address/password/player id are also kept in server_url / room_key /
    uid (what the rest of the app reads); save() copies them back into its entry. Each server has its own folder
    (data_dir) for saves, resume snapshots, backups, the PC database and challenge saves."""

    def __init__(self, path: Path | None = None):
        super().__init__(DEFAULTS)
        self.path = path or (app_dir() / "config.json")
        if self.path.exists():
            try:
                self.update(json.loads(self.path.read_text(encoding="utf-8")))
            except (ValueError, OSError):
                pass
        fresh = not self.get("uid") or not isinstance(self.get("servers"), list) or not self["servers"]
        if not self.get("uid"):
            self["uid"] = str(uuid.uuid4())
        self._init_servers()
        if fresh:
            self.save()

    # --- servers -------------------------------------------------------------------------------------------------
    def _init_servers(self):
        servers = self.get("servers")
        if not isinstance(servers, list) or not servers:
            # older versions had a single server: it becomes the first entry and keeps its folders and player id
            self["servers"] = [{"id": MAIN_SERVER, "label": "", "url": self.get("server_url", ""),
                                "key": self.get("room_key", ""), "uid": self["uid"]}]
            self["server_id"] = MAIN_SERVER
        self["servers"] = [s for s in self["servers"] if isinstance(s, dict) and s.get("id")] or \
            [{"id": MAIN_SERVER, "label": "", "url": self.get("server_url", ""), "key": "", "uid": self["uid"]}]
        if self.get("server_id") not in {s["id"] for s in self["servers"]}:
            first = self["servers"][0]
            self["server_id"] = first["id"]
            self["server_url"], self["room_key"] = first.get("url", ""), first.get("key", "")
            self["uid"] = first.get("uid") or self["uid"]

    @property
    def servers(self) -> list:
        return self["servers"]

    @property
    def server_id(self) -> str:
        return self.get("server_id") or MAIN_SERVER

    def server(self, sid: str) -> dict | None:
        return next((s for s in self["servers"] if s["id"] == sid), None)

    @property
    def active_server(self) -> dict:
        return self.server(self.server_id) or self["servers"][0]

    def _sync_active(self):
        s = self.server(self.server_id)
        if s is not None:
            s["url"], s["key"], s["uid"] = self.get("server_url", ""), self.get("room_key", ""), self["uid"]

    @staticmethod
    def server_label(s: dict) -> str:
        """What the player sees: their own name for it, else the name the server gave itself, else its address."""
        label = (s.get("label") or s.get("seen_name") or "").strip()
        if label:
            return label[:30]
        host = normalize_url(s.get("url", "")).split("://", 1)[-1].rstrip("/")
        return host or "Server"

    def _check_url(self, url: str, skip: str | None = None) -> str:
        url = normalize_url(url)
        if not url_looks_ok(url):
            raise ValueError("Please enter the server address (like ws://1.2.3.4:8765).")
        for s in self["servers"]:
            if s["id"] != skip and _url_key(s.get("url", "")) == _url_key(url):
                raise ValueError(f"You already have that server ({self.server_label(s)}).")
        return url

    def add_server(self, label: str, url: str, key: str) -> dict:
        """A new server with its own player id and folders. Raises ValueError for a bad or duplicate address."""
        self._sync_active()
        s = {"id": "s" + uuid.uuid4().hex[:10], "label": (label or "").strip()[:30], "url": self._check_url(url),
             "key": key or "", "uid": str(uuid.uuid4())}
        self["servers"].append(s)
        self.save()
        return s

    def edit_server(self, sid: str, label: str, url: str, key: str):
        s = self.server(sid)
        if s is None:
            raise ValueError("That server is gone.")
        self._sync_active()
        s["url"], s["key"], s["label"] = self._check_url(url, skip=sid), key or "", (label or "").strip()[:30]
        if sid == self.server_id:
            self["server_url"], self["room_key"] = s["url"], s["key"]
        self.save()

    def move_server(self, sid: str, url: str, key: str) -> bool:
        """The server told us it moved (merged into another): new address/password, same folder + player id.
        Returns True if another entry already points there (the player was on both servers: two saves, two
        characters on the merged server) - that entry keeps its own folder too."""
        s = self.server(sid)
        if s is None:
            raise ValueError("That server is gone.")
        url = normalize_url(url)
        if not url_looks_ok(url):
            raise ValueError("bad address")
        self._sync_active()
        twin = any(o["id"] != sid and _url_key(o.get("url", "")) == _url_key(url) for o in self["servers"])
        if twin and not s.get("label"):
            s["label"] = ((s.get("seen_name") or "Old server")[:22] + " (save)").strip()
        s["url"], s["key"] = url, key or ""
        if not twin:
            s.pop("seen_name", None)      # the new server tells its own name on connect
        if sid == self.server_id:
            self["server_url"], self["room_key"] = s["url"], s["key"]
        self.save()
        return twin

    def remove_server(self, sid: str):
        """Forget a server (not the one you're on). Its saves stay on disk (servers/<id>/) - also for other
        servers that share them (they point to the folder, not to the entry)."""
        if sid == self.server_id or len(self["servers"]) < 2:
            raise ValueError("You can't remove the server you're playing on.")
        self["servers"] = [s for s in self["servers"] if s["id"] != sid]
        self.save()

    def switch_server(self, sid: str):
        """Make another server active: its address, password, player id, folders and last game."""
        new = self.server(sid)
        if new is None:
            raise ValueError("That server is gone.")
        self._sync_active()
        old = self.server(self.server_id)
        if old is not None:
            old["game"] = self.get("current_game")
        self["server_id"] = sid
        self["server_url"], self["room_key"] = new.get("url", ""), new.get("key", "")
        if not new.get("uid"):
            new["uid"] = str(uuid.uuid4())
        self["uid"] = new["uid"]
        if new.get("game"):
            self["current_game"] = new["game"]
        self.pop("active_challenge", None)
        self.save()

    # --- one save on several servers ------------------------------------------------------------------------
    @staticmethod
    def save_folder(s: dict) -> str:
        """Which folder an entry's saves live in: its own, or another server's when it shares that save."""
        return s.get("save_dir") or s["id"]

    def save_owner(self, s: dict) -> dict | None:
        """The entry whose save this one uses (None = its own save)."""
        if not s.get("save_dir"):
            return None
        return next((o for o in self["servers"] if o["id"] == s["save_dir"]), None)

    def share_save(self, sid: str, origin: str | None):
        """Let server `sid` use the saves + PC of server `origin` (None = back to its own saves).
        The player keeps their own identity (uid) on each server; only the folder and the PC store are shared."""
        s = self.server(sid)
        if s is None:
            raise ValueError("That server is gone.")
        if origin in (None, "", sid):
            for k in ("save_dir", "save_uid"):
                s.pop(k, None)
        else:
            o = self.server(origin)
            if o is None:
                raise ValueError("That server is gone.")
            if o.get("save_dir"):
                o = self.server(o["save_dir"]) or o            # no chains: share with the save's real owner
            if o["id"] == sid:
                for k in ("save_dir", "save_uid"):
                    s.pop(k, None)
            else:
                if any(x.get("save_dir") == sid for x in self["servers"]):
                    raise ValueError("Other servers use this server's save - change those first.")
                self._sync_active()
                s["save_dir"], s["save_uid"] = o["id"], o.get("uid") or self["uid"]
        self.save()

    @property
    def data_dir(self) -> Path:
        """This server's folder (or the folder of the server whose save it shares). The first server uses the app
        folder itself (where everything lived before)."""
        return self._folder_path(self.save_folder(self.active_server))

    @staticmethod
    def _folder_path(folder: str) -> Path:
        if folder == MAIN_SERVER:
            return app_dir()
        d = app_dir() / "servers" / "".join(ch for ch in folder if ch.isalnum())[:20]
        (d / "saves").mkdir(parents=True, exist_ok=True)
        return d

    @property
    def pc_path(self) -> Path:
        """The PC store file. A shared save uses the owner's store (named after the owner's player id)."""
        return self.data_dir / "pc" / f"{self.active_server.get('save_uid') or self['uid']}.json"

    @property
    def pc_keys(self) -> tuple:
        """(this server's key, the store's home server key) for PCSync (per-server sync state)."""
        return self.server_id, self.save_folder(self.active_server)

    def save(self):
        self._sync_active()
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
        """A run's own save folder. Challenges belong to the server they're played on (record, partner), not to
        the main save it may share (SPEC.md section 8): always the server's OWN folder, so sharing or un-sharing a
        save never hides a run. A run a 0.7.2 client put in the shared save's folder is still found there."""
        name = "".join(ch for ch in cid if ch.isalnum() or ch in "-_")[:40]
        d = self._folder_path(self.active_server["id"]) / "challenges" / name
        if not d.exists():
            legacy = self.data_dir / "challenges" / name
            if legacy != d and legacy.exists():
                return legacy
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
        return self.data_dir / "saves" / f"{self.game_id}.sav"

    @property
    def resume_path(self) -> Path:
        c = self.challenge
        if c:
            return self.challenge_dir(c["id"]) / "resume.state"
        d = self.data_dir / "resume"
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{self.game_id}.state"

    @property
    def backup_dir(self) -> Path:
        d = self.data_dir / "saves" / "backups"
        d.mkdir(parents=True, exist_ok=True)
        return d
