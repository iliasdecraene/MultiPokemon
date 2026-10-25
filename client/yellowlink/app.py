"""YellowLink main application."""
from __future__ import annotations

import io
import logging
import os
import shutil
import sys
import time
import webbrowser
from dataclasses import dataclass, field

import numpy as np
import pygame

from . import __version__, game, library, panels, pcdb, pvp, sprites, ui, updater, worldview
from .audio import AudioOut
from .config import Config, app_dir
from .diag import Diagnostics, send_report_sync, show_report_window
from .game import GameMemory
from . import crystal
from .challenge import ChallengeMixin
from .net import NetClient

log = logging.getLogger("yellowlink")

FRAME_TIME = 1 / 59.7275
WIDE_MX, WIDE_MY = 80, 72   # extra game pixels on each side in wide view
STATE_SEND_EVERY = 4        # frames
PARTY_CHECK_EVERY = 180     # frames
SRAM_FLUSH_EVERY = 600      # frames (~10 s)
AUTOSAVE_EVERY = 1800       # frames (~30 s) between resume snapshots

KEYMAP = {
    pygame.K_UP: "up", pygame.K_DOWN: "down", pygame.K_LEFT: "left", pygame.K_RIGHT: "right",
    pygame.K_x: "a", pygame.K_z: "b", pygame.K_a: "a", pygame.K_s: "b",
    pygame.K_RETURN: "start", pygame.K_KP_ENTER: "start",
    pygame.K_BACKSPACE: "select", pygame.K_RSHIFT: "select",
}
PAD_BUTTONS = {0: "a", 1: "b", 6: "select", 7: "start", 4: "select", 9: "start"}


def write_atomic(path, data: bytes, attempts: int = 5) -> bool:
    """Write via temp file + replace; tolerate Windows AV/OneDrive locks."""
    tmp = path.with_suffix(".tmp")
    for i in range(attempts):
        try:
            tmp.write_bytes(data)
            os.replace(tmp, path)
            return True
        except OSError:
            log.warning("could not write %s (attempt %d)", path, i + 1)
            time.sleep(0.05 * (i + 1))
    return False


def version_tuple(v: str):
    try:
        return tuple(int(p) for p in v.lstrip("v").split("."))
    except ValueError:
        return (0,)


@dataclass
class Friend:
    uid: str
    name: str
    state: dict | None = None
    sprite: bytes | None = None
    cur_px: float = 0
    cur_py: float = 0
    cur_map: int = -1
    anim: float = 0


@dataclass
class TradeSession:
    trade_id: str
    partner_uid: str
    partner_name: str
    my_slot: int | None = None
    my_mon: object = None
    their_mon: object = None
    me_confirmed: bool = False
    them_confirmed: bool = False
    phase: str = "open"          # open | executing | applied | done
    pending_receive: object = None
    pending_since: float = 0.0
    applied_backup: tuple | None = None  # (slot, original mon, received mon as written)

    def status_text(self):
        if self.phase == "executing":
            return "Trading... close any menus and stand still."
        if self.phase == "applied":
            return "Waiting for your friend's game..."
        if self.my_mon and self.their_mon and not self.me_confirmed:
            return "Both offers are in. Press C to confirm."
        return ""


@dataclass
class GhostBattle:
    opponent_uid: str
    opponent_name: str
    mons: list
    phase: str = "waiting"       # waiting | triggered | running | ending
    snapshot: bytes = b""
    started: float = field(default_factory=time.time)
    end_frames: int = 0
    result: int | None = None


class App(ChallengeMixin):
    def __init__(self, cfg: Config, diag: Diagnostics | None = None):
        self.cfg = cfg
        self.diag = diag
        self.lib = library.Library(cfg, app_dir())
        if cfg.challenge and cfg.challenge["game"] not in self.lib.games:
            log.warning("challenge game %s is not installed; back to the main game", cfg.challenge["game"])
            cfg.pop("active_challenge", None)
            cfg.save()
        self.init_challenges()
        # in a challenge: that run's own game + separate save (Config paths follow active_challenge)
        self.game_id = cfg.challenge["game"] if cfg.challenge else (self.lib.current or "yellow")
        self.save_tag = f"challenge-{cfg.challenge['id']}" if cfg.challenge else self.game_id
        self.game_info = self.lib.info(self.game_id)
        self.travel_to = None
        self.pcsync = pcdb.PCSync(app_dir() / "pc" / f"{cfg['uid']}.json")
        self.pc_codec = None
        self.pc_trust = False        # True once the game's boxes are known to match our last view
        self.panel = None            # None | "multiplayer" | "regions"
        self.panel_sel = None        # selected uid in the multiplayer panel
        self.click_targets = []      # [(screen Rect, callback, label)] rebuilt every frame
        self._last_mode = "play"
        self._err_times: list = []
        self._last_err_toast = 0.0
        self.fps = 0.0
        cfg["rom_path"] = self.lib.rom_path(self.game_id) or cfg.get("rom_path", "")
        # One adapter module per game: game (Yellow, Gen 1) or crystal (Gen 2). Same method names.
        self.gen2 = self.game_id == "crystal"
        self.G = crystal if self.gen2 else game
        self.rom = self.G.RomData.from_file(cfg["rom_path"])
        # Multiplayer features rely on memory addresses verified on these exact ROMs.
        # Live/practice battles and wide view are Yellow-only (self.hooks / self.wv).
        sha = game.sha1_of(cfg["rom_path"])
        self.full = self.game_info.full_support and sha in (game.YELLOW_SHA1, crystal.CRYSTAL_SHA1)
        self.scale = 4
        self.wide = bool(cfg.get("wide_view", False))
        self.wv = None
        self.last_wide = None
        self._wv_errors = 0
        self.mode = "play"           # play | chat | menu | trade | prompt | battle_confirm | help
        self.chat_input = ""
        self.chat_scroll = 0         # lines scrolled up from the newest message
        self.chat_rect = None
        self.chat_log: list = []
        self.toasts: list = []
        self.friends: dict[str, Friend] = {}
        self.roster: list = []
        self.menu_index = 0
        self.trade: TradeSession | None = None
        self.trade_cursor = 0
        self.prompt: dict | None = None
        self.battle_offer: dict | None = None
        self.battle: GhostBattle | None = None
        self.live: pvp.LiveBattle | None = None
        self.update_dl: updater.Download | None = None
        self.restart_into = None
        self.quit_requested = False
        self.hooks: pvp.LinkHooks | None = None
        self.unresolved_trade = None
        self.unresolved_retry = 0.0
        self.pad_held: set = set()
        self.kb_held: set = set()
        self.update_available = None
        self.download_url = ""
        self.frame = 0
        self.last_state_sent: dict | None = None
        self.last_state_time = 0.0
        self.last_sprite_sent: bytes | None = None
        self.last_party_sent = None
        self.my_tiles: bytes | None = None
        self.palette = list(sprites.DEFAULT_PALETTE)
        self.held: set = set()
        self.fast = False
        self.last_sram: bytes | None = None
        self.sprite_cache = sprites.SpriteCache()
        self.clean_frame = None
        self.volume_rect = None
        self.volume_bar = None
        self.dragging_volume = False

        self._skip_text = False
        pygame.mixer.pre_init(48000, -16, 2, 1024)
        pygame.init()
        pygame.key.stop_text_input()
        pygame.display.set_caption(f"YellowLink {__version__} - {self.game_info.title}")
        self._make_window()
        self.fonts = ui.Fonts()
        self.audio = AudioOut(cfg.get("volume", 60))
        self.pads = []
        pygame.joystick.init()
        for i in range(pygame.joystick.get_count()):
            try:
                j = pygame.joystick.Joystick(i)
                j.init()
                self.pads.append(j)
            except pygame.error:
                pass

        self._start_emulator()
        self.net = NetClient(cfg["server_url"], {
            "name": cfg["name"], "uid": cfg["uid"], "key": cfg.get("room_key", ""), "version": __version__})
        self.net.start()
        self.sys_msg(f"Welcome, {cfg['name']}! Press F1 for help, F3 to report a problem.")
        log.info("config: name=%s server=%s rom=%s", cfg["name"], cfg["server_url"], cfg["rom_path"])
        if not cfg.get("seen_help"):
            self.mode = "help"  # show the controls once on first launch
            cfg["seen_help"] = True
            cfg.save()

    # ------------------------------------------------------------------ setup
    def _make_window(self):
        if self.wide:
            self.off_x, self.off_y = WIDE_MX, WIDE_MY
        else:
            self.off_x, self.off_y = 0, 0
        self.view_w, self.view_h = 160 + 2 * self.off_x, 144 + 2 * self.off_y
        self.scale = int(self.cfg.get("wide_scale" if self.wide else "scale", 0) or 0) or self._fit_scale()
        self.game_w, self.game_h = self.view_w * self.scale, self.view_h * self.scale
        h = max(self.game_h, 480)
        self.screen = pygame.display.set_mode((self.game_w + ui.SIDEBAR_W, h))
        self.game_surf = pygame.Surface((self.view_w, self.view_h))
        self.friend_layer = pygame.Surface((self.view_w, self.view_h), pygame.SRCALPHA)
        self.game_y = (h - self.game_h) // 2

    def _fit_scale(self):
        """Largest integer scale that fits the desktop (with the sidebar)."""
        try:
            sizes = pygame.display.get_desktop_sizes()
            dw, dh = sizes[0] if sizes else (1920, 1080)
        except (pygame.error, AttributeError):
            dw, dh = 1920, 1080
        fit = min((dw - ui.SIDEBAR_W - 40) // (160 + 2 * self.off_x), (dh - 100) // (144 + 2 * self.off_y))
        return max(1, min(4, fit))

    def _start_emulator(self):
        from pyboy import PyBoy
        msg = self.cfg.pop("_travel_msg", None)
        if msg:
            self._loading_screen(msg)
        save = self.cfg.save_path
        ram = None
        self.had_save = save.exists() and any(save.read_bytes()[:32768])
        if save.exists():
            shutil.copy2(save, self.cfg.backup_dir / time.strftime(f"{self.save_tag}-%Y%m%d-%H%M%S.sav"))
            self._prune_backups()
            ram = io.BytesIO(save.read_bytes()[:32768])
        else:
            ram = io.BytesIO(bytes(32768))  # keep PyBoy from writing <rom>.ram next to the ROM
        extra = {}
        if self.gen2:                # Crystal's real-time clock lives next to the save
            extra["rtc_file"] = io.BytesIO(self._load_rtc())
        self.pyboy = PyBoy(self.cfg["rom_path"], window="null", ram_file=ram,
                           sound_emulated=True, sound_sample_rate=48000, log_level="ERROR", **extra)
        self.pyboy.set_emulation_speed(0)
        self.game = (crystal.CrystalMemory if self.gen2 else GameMemory)(self.pyboy.memory, self.rom)
        # Hooks patch ROM addresses, so only enable them on the exact ROM they were verified on.
        if self.full and self.gen2:
            self.pc_codec = None if self.chal else crystal.Gen2PC(self.pyboy.memory, self.rom)
            self.pyboy.hook_register(*crystal.HOOK_PC_OPEN, self._hook_safe(self.on_pc_open), None)
            self.pyboy.hook_register(*crystal.HOOK_PC_CLOSE, self._hook_safe(self.on_pc_close), None)
            if self.wide:
                self.wide = False
                self._make_window()
        elif self.full:
            self.hooks = pvp.LinkHooks(self.pyboy, self._send_battle_move)
            self.wv = worldview.WorldView(self.rom.rom, margin_x=WIDE_MX, margin_y=WIDE_MY)
            self.pc_codec = None if self.chal else pcdb.Gen1PC(self.pyboy.memory, self.rom)
            self.pyboy.hook_register(0x08, 0x546F, self._hook_safe(self.on_pc_open), None)    # BillsPC_
            self.pyboy.hook_register(0x08, 0x553E, self._hook_safe(self.on_pc_close), None)   # ExitBillsPC
        else:
            log.info("%s: multiplayer features, live battles and wide view are Yellow-only for now", self.game_id)
            if self.wide:
                self.wide = False
                self._make_window()
        self.last_sram = self._read_sram()
        dev_state = os.environ.get("YL_DEV_LOAD_STATE")
        if dev_state:
            with open(dev_state, "rb") as f:
                self.pyboy.load_state(f)
        elif self._try_resume():
            pass
        elif self.full and self.had_save and self.cfg.get("skip_intro", True):
            self._loading_screen("Loading your adventure...")
            t0 = time.time()
            result = self.G.fast_continue(self.pyboy, on_progress=lambda: self._loading_screen(
                "Loading your adventure..."))
            log.info("fast continue: %s in %.2fs", result, time.time() - t0)

    @property
    def rtc_path(self):
        return self.cfg.save_path.with_suffix(".rtc")

    def _load_rtc(self) -> bytes:
        """Crystal's clock file. PyBoy can't start from an empty one, so make a fresh clock if missing."""
        try:
            data = self.rtc_path.read_bytes()
            if len(data) >= 33:
                return data
        except OSError:
            pass
        import struct
        now = struct.pack("d", time.time())
        return now + bytes(9) + bytes(8) + now

    def _try_resume(self) -> bool:
        """Continue exactly where the player left off (snapshot written on exit / every 30 s)."""
        path = self.cfg.resume_path
        if not self.cfg.get("auto_resume", True) or not path.exists():
            return False
        try:
            with open(path, "rb") as f:
                self.pyboy.load_state(f)
            self.last_sram = self._read_sram()
            self.pc_trust = True     # snapshots are always taken together with a PC commit
            log.info("resumed %s from snapshot (%s)", self.game_id, time.ctime(path.stat().st_mtime))
            return True
        except Exception:  # noqa: BLE001 - corrupt/old snapshot: fall back to the normal save
            log.exception("could not resume from snapshot; using the normal save")
            try:
                path.rename(path.with_suffix(".bad"))
            except OSError:
                pass
            return False

    def autosave(self, force=False) -> bool:
        """Write the resume snapshot. Yellow only snapshots at safe moments (overworld, idle)."""
        if not self.cfg.get("auto_resume", True) or os.environ.get("YL_DEV_LOAD_STATE"):
            return False
        if self.battle or self.live or (self.trade and self.trade.phase != "open"):
            return False
        if self.full and not force and not self.game.is_safe():
            return False
        buf = io.BytesIO()
        try:
            self.pyboy.save_state(buf)
        except Exception:  # noqa: BLE001
            log.exception("snapshot failed")
            return False
        ok = write_atomic(self.cfg.resume_path, buf.getvalue())
        if ok:
            self.flush_sram()
            store = None if self.chal else self.pcsync.commit()
            if store:
                self.net.send({"type": "pc_put", "store": store})
        return ok

    def _loading_screen(self, msg):
        pygame.event.pump()
        self.screen.fill(ui.BG)
        f = pygame.font.Font(None, 40)
        img = f.render(msg, True, ui.ACCENT)
        self.screen.blit(img, img.get_rect(center=(self.screen.get_width() // 2, self.screen.get_height() // 2)))
        pygame.display.flip()

    def _prune_backups(self, keep=15):
        files = sorted(self.cfg.backup_dir.glob(f"{self.save_tag}-*.sav"))
        for p in files[:-keep]:
            try:
                p.unlink()
            except OSError:
                pass

    def _read_sram(self) -> bytes:
        m = self.pyboy.memory
        return b"".join(bytes(m[b, 0xA000:0xBFFF]) + bytes([m[b, 0xBFFF]]) for b in range(4))

    def flush_sram(self, force=False):
        if (self.battle or self.live) and not force:
            return
        data = self._read_sram()
        if data != self.last_sram and write_atomic(self.cfg.save_path, data):
            self.last_sram = data
            log.info("save file written")

    # ------------------------------------------------------------- messages
    def debug_snapshot(self) -> str:
        g = self.game
        try:
            gs = g.status_line()
        except Exception as e:  # noqa: BLE001
            gs = f"(game state unreadable: {e})"
        return (f"mode={self.mode} net={self.net.status}/{self.net.status_detail} rtt={self.net.rtt_ms} "
                f"fps={self.fps:.1f} frame={self.frame} held={sorted(self.held)} fast={self.fast} "
                f"trade={self.trade.phase if self.trade else None} "
                f"battle={self.battle.phase if self.battle else None} "
                f"live={(self.live.phase, self.live.exchange) if self.live else None} friends={len(self.friends)} | {gs}")

    def open_report(self, reason="player report (F3)"):
        self.release_all()
        if not self.diag:
            return
        text = self.diag.report_text(reason, "state: " + self.debug_snapshot())

        def send():
            if self.net.status == "online":
                self.net.send({"type": "bug_report", "text": text, "reason": reason})
                return "Sent! The host can find it on the server."
            return send_report_sync(self.cfg, text, reason)

        show_report_window(text, "YellowLink – report a problem",
                           "This is the game's log. Click \"Send to host\" (or Copy and paste it in a message) "
                           "and say what you were doing when it went wrong.", send, self.diag.dir)

    def on_report_saved(self, msg):
        self.toast("Report received by the server. Thanks!", 4, ui.GOOD)

    def sys_msg(self, s, color=ui.ACCENT):
        log.info("msg: %s", s)
        self.chat_log.append(("", s, color))

    def toast(self, s, secs=4.0, color=ui.TEXT):
        log.info("toast: %s", s)
        self.toasts = [t for t in self.toasts if t[1] > time.time()][-3:]
        self.toasts.append((s, time.time() + secs, color))

    def sorted_roster(self):
        return sorted(self.roster, key=lambda e: (not e.get("online"), e.get("name", "").lower()))

    def is_nearby(self, uid):
        if not self.full:
            return False
        f = self.friends.get(uid)
        return bool(f and f.state and self.game.in_game() and f.state.get("map") == self.game.map_id()
                    and f.state.get("visible") and f.state.get("game", "yellow") == self.game_id
                    and f.state.get("challenge") == (self.chal or {}).get("id"))

    # -------------------------------------------------------------- network
    def handle_net(self):
        for msg in self.net.poll():
            t = msg.get("type")
            h = getattr(self, "on_" + t.lstrip("_"), None)
            if h:
                try:
                    h(msg)
                except Exception:  # noqa: BLE001
                    log.exception("handler %s failed", t)

    def on_connected(self, msg):
        log.info("connected to %s", self.cfg["server_url"])
        if not self.chal:
            self.net.send({"type": "pc_get"})
        self.chal_on_connected()
        self.last_state_sent = None
        self.last_sprite_sent = None
        self.last_party_sent = None
        if self.unresolved_trade:
            self.net.send({"type": "trade_status", "trade_id": self.unresolved_trade[0]})

    def on_trade_status(self, msg):
        if not self.unresolved_trade or msg.get("trade_id") != self.unresolved_trade[0]:
            return
        state = msg.get("state")
        if state == "pending":
            self.unresolved_retry = time.time() + 3  # server still finishing it; ask again shortly
            return
        tid, (slot, original, written) = self.unresolved_trade
        self.unresolved_trade = None
        if state == "complete":
            self.sys_msg("The trade went through. Remember to SAVE in-game.", ui.GOOD)
            return
        self._rollback_trade(slot, original, written)

    def _rollback_trade(self, slot, original, written):
        party = self.game.party()
        if slot < len(party) and party[slot].identity() == written.identity():
            self.game.write_party_slot(slot, original)
            self.last_party_sent = None
            self.sys_msg("The trade didn't complete on your friend's side, so it was undone.", ui.BAD)
        else:
            self.sys_msg("The trade didn't complete and couldn't be undone automatically (party changed).", ui.BAD)

    def on_disconnected(self, msg):
        self.chal_on_disconnected()
        log.warning("disconnected: %s (%s)", self.net.status, msg.get("reason"))
        if self.live:
            self._end_live("Battle ended: connection lost.", ui.BAD)
        self.friends.clear()
        t = self.trade
        if t:
            if t.phase == "applied" and t.applied_backup:
                # We swapped but never heard the result: ask the server after reconnecting.
                self.unresolved_trade = (t.trade_id, t.applied_backup)
                self._end_trade("Connection lost during the trade - checking with the server...", ui.ACCENT)
            else:
                self._end_trade("Trade cancelled (connection lost).", ui.BAD)
        if self.net.status == "rejected":
            self.sys_msg("Server refused connection: " + self.net.status_detail, ui.BAD)

    def on_welcome(self, msg):
        self.sys_msg("Connected to server.", ui.GOOD)
        if msg.get("motd"):
            self.sys_msg(msg["motd"])
        latest = msg.get("latest_client") or ""
        if latest and version_tuple(latest) > version_tuple(__version__):
            self.update_available = latest
            self.download_url = msg.get("download_url", "")
            self.sys_msg(f"Update {latest} available - press U.", ui.ACCENT)
        self.friends.clear()
        for p in msg.get("players", []):
            if p["uid"] != self.cfg["uid"]:
                self.friends[p["uid"]] = Friend(p["uid"], p["name"], p.get("state"))
                self._snap(self.friends[p["uid"]])
        self.roster = msg.get("roster", [])

    def on_player_join(self, msg):
        p = msg["player"]
        if p["uid"] != self.cfg["uid"]:
            self.friends[p["uid"]] = Friend(p["uid"], p["name"], p.get("state"))

    def on_player_leave(self, msg):
        self.friends.pop(msg["uid"], None)

    def on_state(self, msg):
        f = self.friends.get(msg["uid"])
        if not f:
            return
        f.state = msg
        if f.cur_map != msg.get("map"):
            self._snap(f)

    def _snap(self, f: Friend):
        if f.state:
            f.cur_px, f.cur_py, f.cur_map = f.state.get("px", 0), f.state.get("py", 0), f.state.get("map", -1)

    def on_sprite(self, msg):
        f = self.friends.get(msg["uid"])
        if f:
            try:
                data = game.unb64(msg["data"])
                if len(data) == game.SPRITE_BYTES:
                    f.sprite = data
            except ValueError:
                pass

    def on_chat(self, msg):
        if self.chat_scroll:
            self.chat_scroll += 1
        mine = msg.get("uid") == self.cfg["uid"]
        game_id = msg.get("game") or (self.game_id if mine else (self.friends.get(msg.get("uid")) and
                                                                   self.friend_game(msg.get("uid"))))
        region = "Soul Link" if msg.get("challenge") == "soul_link" or (mine and self.chal) \
            else library.region_name(game_id)
        who = msg.get("name", "?") + (f" · {region}" if region else "")
        self.chat_log.append((who, msg.get("text", ""), ui.CHAT_SELF if mine else ui.TEXT))

    def on_system(self, msg):
        self.sys_msg(msg.get("text", ""))

    def on_roster(self, msg):
        self.roster = msg.get("roster", [])

    def on_error(self, msg):
        self.toast(msg.get("message", "error"), color=ui.BAD)
        self.sys_msg("Server: " + msg.get("message", "error"), ui.BAD)

    # trades -------------------------------------------------------------
    def player_region(self, e) -> str:
        """Region label for a roster entry (yourself: the game you're playing right now)."""
        if e.get("uid") == self.cfg["uid"]:
            return "Soul Link" if self.chal else library.region_name(self.game_id)
        if e.get("challenge") == "soul_link":
            return "Soul Link"
        f = self.friends.get(e.get("uid"))
        return library.region_name((f.state or {}).get("game") if f and f.state else e.get("game"))

    def friend_game(self, uid) -> str:
        f = self.friends.get(uid)
        return (f.state or {}).get("game", "yellow") if f else "yellow"

    def on_trade_request(self, msg):
        if (self.chal or not self.full or self.trade or self.battle or self.live or self.prompt
                or self.friend_game(msg.get("from")) != self.game_id):
            self.net.send({"type": "trade_respond", "trade_id": msg["trade_id"], "accept": False})
            return
        self.prompt = {"kind": "trade", "id": msg["trade_id"], "name": msg.get("name", "?"),
                       "until": time.time() + 55, "shown": None}
        self._maybe_show_prompt()

    def _maybe_show_prompt(self):
        if self.prompt and self.mode == "play" and self.prompt["shown"] is None:
            self.prompt["shown"] = time.time()
            self.mode = "prompt"
            self.release_all()

    def on_trade_started(self, msg):
        if self.battle or self.live:
            self.net.send({"type": "trade_cancel", "trade_id": msg["trade_id"]})
            self.toast("Trade cancelled: you're in a battle.", color=ui.BAD)
            return
        self.trade = TradeSession(msg["trade_id"], msg["with"], msg.get("name", "?"))
        self.trade_cursor = 0
        self.mode = "trade"
        self.release_all()

    def on_trade_offer(self, msg):
        t = self.trade
        if t and t.trade_id == msg["trade_id"]:
            try:
                t.their_mon = self.G.Mon.from_wire(msg["mon"])
            except (ValueError, KeyError):
                t.their_mon = None

    def on_trade_confirm_state(self, msg):
        t = self.trade
        if t and t.trade_id == msg["trade_id"]:
            t.me_confirmed, t.them_confirmed = bool(msg.get("you")), bool(msg.get("them"))

    def on_trade_execute(self, msg):
        t = self.trade
        if not t or t.trade_id != msg["trade_id"]:
            self.net.send({"type": "trade_done", "trade_id": msg["trade_id"], "ok": False})
            return
        try:
            t.pending_receive = self.G.Mon.from_wire(msg["receive"])
        except (ValueError, KeyError):
            self.net.send({"type": "trade_done", "trade_id": t.trade_id, "ok": False})
            return
        if msg.get("give_slot") != t.my_slot:
            self.net.send({"type": "trade_done", "trade_id": t.trade_id, "ok": False})
            return
        t.phase = "executing"
        t.pending_since = time.time()
        log.info("trade %s executing: give slot %s, receive species %s", t.trade_id, t.my_slot,
                 t.pending_receive.species)

    def _try_apply_trade(self):
        t = self.trade
        if not t or t.phase != "executing":
            return
        if time.time() - t.pending_since > 30:
            self.net.send({"type": "trade_done", "trade_id": t.trade_id, "ok": False})
            t.phase = "done"
            self._end_trade("Trade failed: your game was busy.", ui.BAD)
            return
        if self.battle or self.net.status != "online" or not self.game.is_safe():
            return
        party = self.game.party()
        if t.my_slot >= len(party) or party[t.my_slot].identity() != t.my_mon.identity():
            self.net.send({"type": "trade_done", "trade_id": t.trade_id, "ok": False})
            t.phase = "done"
            self._end_trade("Trade failed: your party changed.", ui.BAD)
            return
        # Tell the server first; only write RAM if the message could be queued.
        if not self.net.send({"type": "trade_done", "trade_id": t.trade_id, "ok": True}):
            return
        written = self.game.apply_trade(t.my_slot, t.my_mon, t.pending_receive)
        t.applied_backup = (t.my_slot, t.my_mon, written)
        t.phase = "applied"
        if written.species != t.pending_receive.species:
            self.toast(f"{self.rom.species_name(t.pending_receive.species)} evolved into "
                       f"{self.rom.species_name(written.species)}!", 6, ui.ACCENT)

    def on_trade_complete(self, msg):
        t = self.trade
        if t and t.trade_id == msg["trade_id"]:
            got = t.applied_backup[2] if t.applied_backup else None
            name = f"{got.nick_str()}" if got else "your new Pokémon"
            self._end_trade(f"Trade complete! Say hi to {name}. Remember to SAVE in-game.", ui.GOOD)
            self.last_party_sent = None

    def on_trade_cancelled(self, msg):
        t = self.trade
        if not t or t.trade_id != msg.get("trade_id"):
            if not t:
                self.toast("Trade request ended: " + msg.get("reason", ""), 4, ui.DIM)
            return
        if t.applied_backup:
            # Our side already swapped but the partner's failed: undo to avoid duplicates.
            self._rollback_trade(*t.applied_backup)
        self._end_trade("Trade cancelled: " + msg.get("reason", ""), ui.BAD)

    def _end_trade(self, s, color):
        self.trade = None
        if self.mode == "trade":
            self.mode = "play"
        self.toast(s, 6, color)
        self.sys_msg(s, color)

    # battles ------------------------------------------------------------
    def on_party_data(self, msg):
        if not self.full or self.gen2:
            return
        if msg.get("error") or not msg.get("mons"):
            self.toast("That trainer has no Pokémon to battle yet.", color=ui.BAD)
            return
        try:
            mons = [game.Mon.from_wire(m) for m in msg["mons"]][:6]
        except (ValueError, KeyError):
            self.toast("Couldn't read their team.", color=ui.BAD)
            return
        self.battle_offer = {"uid": msg["uid"], "name": msg.get("name", "?"), "mons": mons}
        self.mode = "battle_confirm"
        self.release_all()

    def start_battle(self):
        o = self.battle_offer
        self.battle_offer = None
        self.mode = "play"
        if not self.game.party():
            self.toast("You need a Pokémon first!", color=ui.BAD)
            return
        if self.trade or self.live:
            self.toast("Finish what you're doing first.", color=ui.BAD)
            return
        self.battle = GhostBattle(o["uid"], o["name"], o["mons"])
        self.toast(f"Get ready to battle {o['name']}! Stand still...", 4, ui.ACCENT)

    def update_battle(self):
        b = self.battle
        if not b:
            return
        g = self.game
        if b.phase == "waiting":
            if time.time() - b.started > 30:
                self.battle = None
                self.toast("Battle cancelled: close menus and stand still in the overworld.", 6, ui.BAD)
                return
            if g.is_safe() and not self.trade:
                self.flush_sram(force=True)
                buf = io.BytesIO()
                self.pyboy.save_state(buf)
                b.snapshot = buf.getvalue()
                g.heal_party()
                g.trigger_trainer_battle()
                b.phase = "triggered"
                b.started = time.time()
        elif b.phase == "triggered":
            if g.m[game.IS_IN_BATTLE] == 2:
                g.inject_enemy(b.mons, b.opponent_name)
                b.phase = "running"
                log.info("ghost battle vs %s running (%d mons)", b.opponent_name, len(b.mons))
            elif time.time() - b.started > 15:
                self._restore_battle()
                self.toast("Battle failed to start.", color=ui.BAD)
        elif b.phase == "running":
            if g.m[game.IS_IN_BATTLE] == 0:
                b.result = g.m[game.BATTLE_RESULT]
                log.info("ghost battle ended, result=%s", b.result)
                b.phase = "ending"
                b.end_frames = 20
        elif b.phase == "ending":
            b.end_frames -= 1
            if b.end_frames <= 0:
                won = b.result == 0
                self._restore_battle()
                self.net.send({"type": "battle_result", "opponent_uid": b.opponent_uid, "won": won})
                if won:
                    self.toast(f"You beat {b.opponent_name}'s team!", 6, ui.GOOD)
                else:
                    self.toast(f"{b.opponent_name}'s team was too strong this time.", 6, ui.BAD)

    def _restore_battle(self):
        b = self.battle
        self.battle = None
        if b and b.snapshot:
            self.pyboy.load_state(io.BytesIO(b.snapshot))
        self.release_all()

    # live PvP battles -------------------------------------------------------
    def _send_battle_move(self, s, n, v, check):
        self.net.send({"type": "battle_move", "battle_id": s.battle_id, "n": n, "v": v, "check": check})

    def on_battle_request(self, msg):
        if self.chal or not self.full or self.trade or self.battle or self.live or self.prompt or not self.hooks or not self.game.party():
            self.net.send({"type": "battle_respond", "battle_id": msg["battle_id"], "accept": False})
            return
        self.prompt = {"kind": "battle", "id": msg["battle_id"], "name": msg.get("name", "?"),
                       "until": time.time() + 55, "shown": None}
        self._maybe_show_prompt()

    def on_battle_prepare(self, msg):
        if self.live or self.battle or self.trade or not self.hooks:
            self.net.send({"type": "battle_leave", "battle_id": msg["battle_id"]})
            return
        self.live = pvp.LiveBattle(msg["battle_id"], msg.get("with", ""), msg.get("name", "?"),
                                   bool(msg.get("master")))
        if self.mode in ("menu", "help", "battle_confirm"):
            self.mode = "play"
        self.toast(f"Battle vs {self.live.opponent_name}! Close any menus and stand still...", 5, ui.ACCENT)
        log.info("live battle %s prepare vs %s (master=%s)", self.live.battle_id, self.live.opponent_name,
                 self.live.master)

    def on_battle_start(self, msg):
        s = self.live
        if not s or s.battle_id != msg.get("battle_id") or s.phase != "ready":
            return
        try:
            mons = [game.Mon.from_wire(m) for m in msg.get("mons", [])][:6]
        except (ValueError, KeyError):
            mons = []
        rns = msg.get("rns") or []
        if not mons or len(rns) != 10:
            self.net.send({"type": "battle_leave", "battle_id": s.battle_id})
            self._end_live("Battle cancelled: bad data from the server.", ui.BAD)
            return
        s.master = bool(msg.get("master", s.master))
        pvp.setup_battle(self.game, s, mons, msg.get("trainer") or s.opponent_name, rns)
        self.hooks.session = s
        s.phase = "starting"
        s.started = time.time()
        log.info("live battle %s starting: %d enemy mons", s.battle_id, len(mons))

    def on_battle_move(self, msg):
        s = self.live
        if not s or s.battle_id != msg.get("battle_id"):
            return
        n, v = msg.get("n"), msg.get("v")
        if isinstance(n, int) and isinstance(v, int):
            s.inbox[n] = v & 0x0F
            if isinstance(msg.get("check"), str):
                s.their_checks[n] = msg["check"]
                pvp.compare_checks(s, n)

    def on_battle_aborted(self, msg):
        s = self.live
        reason = msg.get("reason", "")
        if s and s.battle_id == msg.get("battle_id"):
            texts = {"opponent left": f"{s.opponent_name} left the battle.",
                     "opponent disconnected": f"{s.opponent_name} disconnected.",
                     "timeout": "The battle didn't start in time.",
                     "declined": f"{s.opponent_name} declined."}
            self._end_live(texts.get(reason, "Battle cancelled."), ui.BAD)
        elif not s and reason == "declined":
            self.toast("Your battle challenge was declined.", 4, ui.DIM)
        elif not s and reason == "timeout":
            self.toast("Your battle challenge got no answer.", 4, ui.DIM)

    def update_live(self):
        s = self.live
        if not s:
            return
        g = self.game
        if s.phase == "preparing":
            if time.time() - s.started > 40:
                self.net.send({"type": "battle_leave", "battle_id": s.battle_id})
                self._end_live("Battle cancelled: close menus and stand still in the overworld.", ui.BAD)
                return
            if g.is_safe() and not self.trade and not self.battle:
                party = g.party_checked()
                if not party:
                    return
                buf = io.BytesIO()
                self.pyboy.save_state(buf)
                s.snapshot = buf.getvalue()
                self.release_all()
                g.heal_party()
                party = g.party()
                self.net.send({"type": "battle_ready", "battle_id": s.battle_id,
                               "mons": [m.to_wire() for m in party], "trainer": g.player_name()})
                s.phase = "ready"
                s.started = time.time()
        elif s.phase == "starting":
            if g.m[game.IS_IN_BATTLE]:
                s.phase = "running"
                log.info("live battle %s running", s.battle_id)
            elif time.time() - s.started > 15:
                self.net.send({"type": "battle_leave", "battle_id": s.battle_id})
                self._end_live("Battle failed to start.", ui.BAD)
        elif s.phase == "running":
            if g.m[game.IS_IN_BATTLE] == 0:
                s.result = g.m[game.BATTLE_RESULT]
                s.phase = "ending"
                s.end_frames = 200  # let the result screen show
                log.info("live battle %s finished, result=%s", s.battle_id, s.result)
        elif s.phase == "ending":
            s.end_frames -= 1
            if s.end_frames <= 0:
                self.net.send({"type": "battle_end", "battle_id": s.battle_id, "result": int(s.result or 0)})
                msg = {0: f"You beat {s.opponent_name}!", 1: f"{s.opponent_name} won this time.",
                       2: f"It's a draw with {s.opponent_name}!"}.get(s.result, "Battle over.")
                self._end_live(msg, ui.GOOD if s.result == 0 else ui.TEXT)

    def _end_live(self, message, color):
        s = self.live
        self.live = None
        if self.hooks:
            self.hooks.session = None
        if s and s.snapshot:
            self.pyboy.load_state(io.BytesIO(s.snapshot))
        pvp.clear_link_state(self.game)
        self.release_all()
        if message:
            self.toast(message, 6, color)
            self.sys_msg(message, color)

    # ---------------------------------------------------------- publishing
    def publish(self):
        if self.net.status != "online" or not self.full:
            return
        g = self.game
        if not g.in_game():
            return
        if self.frame % STATE_SEND_EVERY == 0:
            st = g.position()
            st["visible"] = g.in_game()  # stay visible (at your last spot) in menus and battles too
            st["game"] = self.game_id
            if self.chal:
                st["challenge"] = self.chal["id"]
            st["type"] = "state"
            now = time.time()
            if st != self.last_state_sent or now - self.last_state_time > 2:
                self.net.send(st)
                self.last_state_sent, self.last_state_time = st, now
        if self.frame % 30 == 0:
            tiles = g.sprite_tiles()
            if tiles and not g.walking():
                self.my_tiles = tiles
                pal = sprites.sample_palette(self.pyboy.screen.ndarray, tiles, g.player_img())
                if pal:
                    self.palette = pal
                if tiles != self.last_sprite_sent:
                    if self.net.send({"type": "sprite", "data": game.b64(tiles)}):
                        self.last_sprite_sent = tiles
        if self.frame % PARTY_CHECK_EVERY == 0 and g.is_safe() and not self.battle and not self.live \
                and not self.chal:
            party = g.party_checked()
            if party is None:
                return
            key = tuple(m.data + m.ot + m.nick for m in party)
            if key != self.last_party_sent:
                if self.net.send({"type": "party", "mons": [m.to_wire() for m in party],
                                  "trainer": g.player_name(), "badges": g.badges()}):
                    self.last_party_sent = key

    # ---------------------------------------------------------------- input
    # PC database ---------------------------------------------------------------
    def _hook_safe(self, fn):
        def wrapped(_ctx):
            try:
                fn()
            except Exception:  # noqa: BLE001 - never break the emulator from a hook
                log.exception("PC hook failed")
        return wrapped

    def on_pc_open(self):
        if self.chal:
            if self.referee:
                self.referee.on_pc_open()
            return
        if not self.pc_codec:
            return
        if self.gen2 and not self.pc_codec.initialised():
            log.info("PC opened before the first save: Crystal's boxes don't exist yet, not syncing")
            return
        boxes = self.pcsync.open_session(self.game_id, self.pc_codec, trust_game=self.pc_trust)
        self.pc_codec.write_boxes(boxes)
        self.pc_trust = True
        log.info("PC opened: %d mons in the database", self.pcsync.count())

    def on_pc_close(self):
        if self.chal:
            if self.referee:
                self.referee.on_pc_close()
            return
        if not self.pc_codec or not self.pcsync.session_open:
            return
        report = self.pcsync.close_session(self.game_id, self.pc_codec, self.G.party_records(self.game))
        for slot in sorted(report["standin_party_slots"], reverse=True):
            self.game.remove_party_slot(slot)
        if report["standins_returned"]:
            names = ", ".join(n.title() for n in report["standins_returned"][:3])
            self.toast(f"{names} can't leave the PC here - they live in another region.", 6, ui.ACCENT)
        log.info("PC closed: +%d -%d (pending=%s)", report["added"], report["removed"], self.pcsync.pending)

    def on_pc_data(self, msg):
        store = msg.get("store")
        local = self.pcsync.committed
        if store is None or store.get("rev", 0) < local.get("rev", 0):
            if local.get("rev", 0) > 0:
                self.net.send({"type": "pc_put", "store": local})     # server is behind: catch it up
        elif store.get("rev", 0) > local.get("rev", 0):
            if self.pcsync.adopt_remote(store):
                self.pc_trust = False                                 # boxes in this game are now stale
                self.sys_msg("Your PC boxes were updated from the server.", ui.GOOD)

    def on_pc_conflict(self, msg):
        if self.pcsync.adopt_remote(msg.get("store")):
            self.pc_trust = False
            self.sys_msg("Your PC boxes were updated from the server.", ui.GOOD)
        else:
            log.warning("PC store conflict could not be resolved yet (pending changes)")

    def on_pc_saved(self, msg):
        log.info("PC store saved on the server (rev %s)", msg.get("rev"))

    # chat / view --------------------------------------------------------------
    def focus_chat(self):
        if self.mode not in ("play", "panel", "chat"):
            return
        self.close_panel()
        self.release_all()
        self.mode = "chat"
        self._skip_text = False
        pygame.key.start_text_input()

    def unfocus_chat(self):
        if self.mode == "chat":
            self.mode = "play"
            pygame.key.stop_text_input()

    def send_chat(self):
        msg = self.chat_input.strip()
        if msg:
            if not self.net.send({"type": "chat", "text": msg}):
                self.sys_msg("(not connected - message not sent)", ui.BAD)
        self.chat_input = ""
        self.chat_scroll = 0
        self.unfocus_chat()

    def chat_to_bottom(self):
        self.chat_scroll = 0

    def toggle_wide(self):
        if not self.wv:
            self.toast("Wide view works in Yellow for now.", 3, ui.BAD)
            return
        self.wide = not self.wide
        self.cfg["wide_view"] = self.wide
        self.cfg.save()
        self.last_wide = None
        self._make_window()
        self.toast("Wide view on" if self.wide else "Wide view off", 1.5)

    # panels ------------------------------------------------------------------
    def toggle_panel(self, name):
        if self.mode == "panel" and self.panel == name:
            self.close_panel()
            return
        if self.mode not in ("play", "panel"):
            return  # a trade window, prompt or chat is open
        self.release_all()
        self.panel = name
        self.mode = "panel"

    def close_panel(self):
        self.panel = None
        if self.mode == "panel":
            self.mode = "play"

    def select_player(self, uid):
        self.panel_sel = None if self.panel_sel == uid else uid

    def action_trade(self, e):
        if self.chal:
            self.toast("Not during a Soul Link - it's just you and your partner.", 4, ui.BAD)
            return
        if not e.get("online"):
            self.toast("They need to be online to trade.", color=ui.BAD)
        elif not self.full:
            self.toast("Trading isn't available in this game.", color=ui.BAD)
        elif self.friend_game(e["uid"]) != self.game_id:
            self.toast(f"{e.get('name')} is playing another game - trades work within the same game for now.",
                       5, ui.BAD)
        elif not self.game.party():
            self.toast("You need a Pokémon first!", color=ui.BAD)
        else:
            self.net.send({"type": "trade_request", "to": e["uid"]})
            self.toast(f"Trade request sent to {e.get('name')}...")
            self.close_panel()

    def action_battle(self, e):
        if self.chal:
            self.toast("Not during a Soul Link - it's just you and your partner.", 4, ui.BAD)
            return
        if not e.get("online"):
            self.toast("They need to be online for a live battle. Try Practice.", color=ui.BAD)
        elif not self.full or not self.hooks:
            self.toast("Live battles work in Yellow for now.", color=ui.BAD)
        elif not self.game.party():
            self.toast("You need a Pokémon first!", color=ui.BAD)
        elif self.trade or self.battle or self.live:
            self.toast("Finish what you're doing first.", color=ui.BAD)
        else:
            self.net.send({"type": "battle_request", "to": e["uid"]})
            self.toast(f"Battle request sent to {e.get('name')}...")
            self.close_panel()

    def action_practice(self, e):
        if self.chal:
            self.toast("Not during a Soul Link - it's just you and your partner.", 4, ui.BAD)
            return
        if not self.full or self.gen2:
            self.toast("Practice battles work in Yellow for now.", color=ui.BAD)
            return
        self.net.send({"type": "get_party", "uid": e["uid"]})
        self.close_panel()

    def travel(self, game_id):
        if self.chal:
            self.leave_to_main("travelled", travel_to=game_id)
            return
        if self.trade or self.battle or self.live:
            self.toast("Finish your trade or battle first.", color=ui.BAD)
            return
        info = self.lib.info(game_id)
        self.cfg["_travel_msg"] = f"Travelling to {info.title}..."
        self.travel_to = game_id
        self.quit_requested = True

    def pick_region_game(self, region_id, game_id):
        """Remember which version to use for a region; if we're in that region, switch to it."""
        picks = self.cfg.setdefault("region_game", {})
        picks[region_id] = game_id
        self.cfg.save()
        if self.game_info.region == region_id and game_id != self.game_id:
            self.travel(game_id)

    def add_rom_dialog(self):
        self.release_all()
        try:
            import tkinter as tk
            from tkinter import filedialog
            root = tk.Tk()
            root.withdraw()
            path = filedialog.askopenfilename(title="Add a Pokémon game file",
                                              filetypes=[("Game Boy ROM", "*.gb *.gbc"), ("All files", "*.*")])
            root.destroy()
        except Exception:  # noqa: BLE001
            log.exception("file dialog failed")
            self.toast("Couldn't open the file picker.", color=ui.BAD)
            return
        if not path:
            return
        try:
            info = self.lib.add_rom(path)
            self.toast(f"Added {info.title}! Click Travel to play it.", 5, ui.GOOD)
        except ValueError as e:
            self.toast(str(e), 6, ui.BAD)
        except OSError as e:
            self.toast(f"Couldn't copy the file: {e}", 6, ui.BAD)

    def set_volume(self, percent):
        percent = max(0, min(100, int(percent)))
        self.audio.volume = percent / 100
        if self.audio.muted and percent > 0:
            self.audio.set_muted(False)

    def _set_volume_from_x(self, x):
        bar = self.volume_bar
        if bar and bar.w:
            self.set_volume(round((x - bar.x) / bar.w * 100))

    def release_all(self):
        for b in list(self.held):
            self.pyboy.button_release(b)
        self.held.clear()
        self.kb_held.clear()
        self.pad_held.clear()

    def press(self, b, down, source="kb"):
        if self.mode != "play" or (self.battle and self.battle.phase == "triggered"):
            return
        if self.live and self.live.phase in ("ready", "starting"):
            return
        src = self.pad_held if source == "pad" else self.kb_held
        if down:
            src.add(b)
        else:
            src.discard(b)
        want = b in self.kb_held or b in self.pad_held
        if want and b not in self.held:
            self.pyboy.button_press(b)
            self.held.add(b)
        elif not want and b in self.held:
            self.pyboy.button_release(b)
            self.held.discard(b)

    def handle_events(self):
        if self.quit_requested:
            return False
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                return False
            if ev.type == pygame.KEYDOWN:
                self.on_key(ev)
            elif ev.type == pygame.KEYUP:
                if ev.key in KEYMAP:
                    self.press(KEYMAP[ev.key], False)
                if ev.key == pygame.K_SPACE:
                    self.fast = False
            elif ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1:
                if self.volume_rect and self.volume_rect.collidepoint(ev.pos):
                    self.dragging_volume = True
                    self._set_volume_from_x(ev.pos[0])
                else:
                    hit = False
                    for rect, cb, _label in reversed(self.click_targets):
                        if rect.collidepoint(ev.pos):
                            cb()
                            hit = True
                            break
                    if not hit and self.mode == "chat" and not (self.chat_rect and self.chat_rect.collidepoint(ev.pos)):
                        self.unfocus_chat()   # clicked away from the chat box
            elif ev.type == pygame.MOUSEWHEEL:
                if self.chat_rect and self.chat_rect.collidepoint(pygame.mouse.get_pos()):
                    self.chat_scroll = max(0, self.chat_scroll + ev.y * 3)
            elif ev.type == pygame.MOUSEMOTION and self.dragging_volume:
                self._set_volume_from_x(ev.pos[0])
            elif ev.type == pygame.MOUSEBUTTONUP and ev.button == 1 and self.dragging_volume:
                self.dragging_volume = False
                self.cfg["volume"] = int(round(self.audio.volume * 100))
                self.cfg.save()
            elif ev.type == pygame.TEXTINPUT and self.mode == "chat":
                if self._skip_text:
                    self._skip_text = False
                    continue
                if len(self.chat_input) < 200:
                    self.chat_input += ev.text
            elif ev.type == pygame.JOYBUTTONDOWN and ev.button in PAD_BUTTONS:
                log.info("pad button %s", ev.button)
                self.press(PAD_BUTTONS[ev.button], True, "pad")
            elif ev.type == pygame.JOYBUTTONUP and ev.button in PAD_BUTTONS:
                self.press(PAD_BUTTONS[ev.button], False, "pad")
            elif ev.type == pygame.JOYHATMOTION:
                x, y = ev.value
                for b, on in (("left", x < 0), ("right", x > 0), ("up", y > 0), ("down", y < 0)):
                    self.press(b, on, "pad")
            elif ev.type == pygame.JOYAXISMOTION and ev.axis in (0, 1):
                v = ev.value
                if ev.axis == 0:
                    self.press("left", v < -0.5, "pad")
                    self.press("right", v > 0.5, "pad")
                else:
                    self.press("up", v < -0.5, "pad")
                    self.press("down", v > 0.5, "pad")
            elif ev.type == pygame.JOYDEVICEADDED:
                try:
                    j = pygame.joystick.Joystick(ev.device_index)
                    j.init()
                    self.pads.append(j)
                except pygame.error:
                    pass
        return True

    def on_key(self, ev):
        k = ev.key
        if self.mode == "panel":
            if k in (pygame.K_ESCAPE, pygame.K_TAB):
                self.close_panel()
            elif self.panel == "multiplayer":
                others = [e for e in self.sorted_roster() if e.get("uid") != self.cfg["uid"]]
                uids = [e.get("uid") for e in others]
                if uids and k in (pygame.K_UP, pygame.K_DOWN):
                    i = uids.index(self.panel_sel) if self.panel_sel in uids else -1
                    i = max(0, i - 1) if k == pygame.K_UP else min(len(uids) - 1, i + 1)
                    self.panel_sel = uids[i]
                elif self.panel_sel in uids and k in (pygame.K_b, pygame.K_r, pygame.K_p):
                    e = others[uids.index(self.panel_sel)]
                    {pygame.K_b: self.action_battle, pygame.K_r: self.action_trade,
                     pygame.K_p: self.action_practice}[k](e)
            return
        if self.mode != "chat":
            log.info("key %s (mode %s)", pygame.key.name(k), self.mode)
        if k == pygame.K_F3:
            self.open_report()
            return
        if k == pygame.K_F4 and self.live:
            self.net.send({"type": "battle_leave", "battle_id": self.live.battle_id})
            self._end_live("You left the battle.", ui.DIM)
            return
        if self.mode == "chat":
            if k == pygame.K_RETURN or k == pygame.K_KP_ENTER:
                self.send_chat()
            elif k == pygame.K_ESCAPE:
                self.chat_input = ""
                self.mode = "play"
                pygame.key.stop_text_input()
            elif k == pygame.K_BACKSPACE:
                self.chat_input = self.chat_input[:-1]
            return
        if self.mode == "help":
            if k in (pygame.K_ESCAPE, pygame.K_F1, pygame.K_RETURN):
                self.mode = "play"
            return
        if self.mode == "menu":
            others = [e for e in self.sorted_roster() if e.get("uid") != self.cfg["uid"]]
            if k == pygame.K_ESCAPE or k == pygame.K_TAB:
                self.mode = "play"
            elif k == pygame.K_UP:
                self.menu_index = max(0, self.menu_index - 1)
            elif k == pygame.K_DOWN:
                self.menu_index = min(max(0, len(others) - 1), self.menu_index + 1)
            elif others and k in (pygame.K_r, pygame.K_b, pygame.K_p):
                e = others[min(self.menu_index, len(others) - 1)]
                if k == pygame.K_r:
                    if not e.get("online"):
                        self.toast("They need to be online to trade.", color=ui.BAD)
                    elif not self.game.party():
                        self.toast("You need a Pokémon first!", color=ui.BAD)
                    else:
                        self.net.send({"type": "trade_request", "to": e["uid"]})
                        self.toast(f"Trade request sent to {e.get('name')}...")
                        self.mode = "play"
                elif k == pygame.K_b:
                    if not e.get("online"):
                        self.toast("They need to be online for a live battle. Try P (practice).", color=ui.BAD)
                    elif not self.hooks:
                        self.toast("Live battles need the USA/Europe ROM.", color=ui.BAD)
                    elif not self.game.party():
                        self.toast("You need a Pokémon first!", color=ui.BAD)
                    elif self.trade or self.battle or self.live:
                        self.toast("Finish what you're doing first.", color=ui.BAD)
                    else:
                        self.net.send({"type": "battle_request", "to": e["uid"]})
                        self.toast(f"Battle request sent to {e.get('name')}...")
                        self.mode = "play"
                else:
                    self.net.send({"type": "get_party", "uid": e["uid"]})
                    self.mode = "play"
            return
        if self.mode == "chal_popup":
            if k in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_ESCAPE, pygame.K_SPACE):
                self.chal_close_popup()
            return
        if self.mode == "prompt":
            p = self.prompt
            if not p or time.time() - (p.get("shown") or 0) < 0.5:
                return
            if k in (pygame.K_y, pygame.K_RETURN, pygame.K_n, pygame.K_ESCAPE):
                accept = k in (pygame.K_y, pygame.K_RETURN)
                if p["kind"] == "challenge":
                    self.respond_challenge(accept)
                    self.release_all()
                    return
                if p["kind"] == "battle":
                    self.net.send({"type": "battle_respond", "battle_id": p["id"], "accept": accept})
                else:
                    self.net.send({"type": "trade_respond", "trade_id": p["id"], "accept": accept})
                self.mode = "play"
                self.prompt = None
                self.release_all()
            return
        if self.mode == "update_confirm":
            if k in (pygame.K_RETURN, pygame.K_KP_ENTER):
                urls = updater.asset_urls(self.download_url, self.update_available)
                self.update_dl = updater.Download(urls[0], urls[1], self.update_available)
                self.toast("Downloading the update - keep playing!", 4)
                self.mode = "play"
            elif k == pygame.K_ESCAPE:
                self.mode = "play"
            return
        if self.mode == "update_ready":
            if k in (pygame.K_RETURN, pygame.K_KP_ENTER):
                if self.trade or self.battle or self.live:
                    self.toast("Finish your trade/battle first.", 3, ui.BAD)
                else:
                    self.restart_into = self.update_dl.path
                    self.quit_requested = True
                self.mode = "play"
            elif k == pygame.K_ESCAPE:
                self.mode = "play"
            return
        if self.mode == "battle_confirm":
            if k in (pygame.K_RETURN, pygame.K_KP_ENTER):
                self.start_battle()
            elif k == pygame.K_ESCAPE:
                self.battle_offer = None
                self.mode = "play"
            return
        if self.mode == "trade":
            t = self.trade
            party = self.game.party()
            if t.phase != "open":
                return
            if k == pygame.K_UP:
                self.trade_cursor = max(0, self.trade_cursor - 1)
            elif k == pygame.K_DOWN:
                self.trade_cursor = min(max(0, len(party) - 1), self.trade_cursor + 1)
            elif k in (pygame.K_RETURN, pygame.K_KP_ENTER) and party and self.game.party_checked():
                slot = min(self.trade_cursor, len(party) - 1)
                t.my_slot, t.my_mon = slot, party[slot]
                t.me_confirmed = t.them_confirmed = False
                self.net.send({"type": "trade_offer", "trade_id": t.trade_id, "slot": slot,
                               "mon": party[slot].to_wire()})
            elif k == pygame.K_c and t.my_mon and t.their_mon:
                self.net.send({"type": "trade_confirm", "trade_id": t.trade_id})
            elif k == pygame.K_ESCAPE:
                self.net.send({"type": "trade_cancel", "trade_id": t.trade_id})
                self._end_trade("Trade cancelled.", ui.DIM)
            return

        # play mode
        if k in KEYMAP:
            self.press(KEYMAP[k], True)
        elif k == pygame.K_t:
            self.focus_chat()
            self._skip_text = True  # the "t" that opened chat arrives as TEXTINPUT next
        elif k == pygame.K_TAB:
            if self.battle or self.live:
                return
            self.toggle_panel("multiplayer")
        elif k == pygame.K_F1:
            self.release_all()
            self.mode = "help"
        elif k == pygame.K_SPACE:
            self.fast = True
        elif k in (pygame.K_MINUS, pygame.K_KP_MINUS, pygame.K_EQUALS, pygame.K_PLUS, pygame.K_KP_PLUS):
            step = -10 if k in (pygame.K_MINUS, pygame.K_KP_MINUS) else 10
            self.set_volume(int(round(self.audio.volume * 100)) + step)
            self.cfg["volume"] = int(round(self.audio.volume * 100))
            self.cfg.save()
            self.toast(f"Volume {self.cfg['volume']}%", 1.2)
        elif k == pygame.K_v:
            self.toggle_wide()
        elif k == pygame.K_m:
            self.audio.set_muted(not self.audio.muted)
            self.toast("Sound off" if self.audio.muted else "Sound on", 1.5)
        elif k == pygame.K_n:
            self.cfg["show_names"] = not self.cfg.get("show_names", True)
            self.cfg.save()
        elif k in (pygame.K_F5, pygame.K_F6):
            self.cfg["wide_scale" if self.wide else "scale"] = max(1, min(6, self.scale + (1 if k == pygame.K_F6 else -1)))
            self.cfg.save()
            self._make_window()
        elif k == pygame.K_u and self.update_available:
            self.release_all()
            urls = updater.asset_urls(self.download_url, self.update_available)
            if not updater.can_self_update() or not urls:
                webbrowser.open(self.download_url or "https://github.com")
            elif self.update_dl and self.update_dl.status == "ready":
                self.mode = "update_ready"
            elif self.update_dl and self.update_dl.status == "downloading":
                self.toast("Update is downloading...", 2)
            else:
                self.mode = "update_confirm"
        elif k == pygame.K_F2:
            self.toast("Settings open after you close the game (run with --setup).", 4)
            self.cfg["_open_setup"] = True

    # --------------------------------------------------------------- render
    def update_friends(self):
        """Glide remote players toward their last reported position (1-2 px/frame)."""
        for f in self.friends.values():
            st = f.state
            if not st:
                continue
            if f.cur_map != st.get("map"):
                self._snap(f)
            tx, ty = st.get("px", 0), st.get("py", 0)
            dx, dy = tx - f.cur_px, ty - f.cur_py
            if abs(dx) > 48 or abs(dy) > 48:
                f.cur_px, f.cur_py = tx, ty
                f.anim = 0
            elif dx or dy:
                spd = 2 if max(abs(dx), abs(dy)) > 20 else 1
                f.cur_px += max(-spd, min(spd, dx))
                f.cur_py += max(-spd, min(spd, dy))
                f.anim += 1
            else:
                f.anim = 0

    def menu_mask(self, arr):
        """8x8-cell mask (18x20) of screen areas covered by a menu/text box, or None if nothing is.

        While a menu is open the player can't move, so any cell that differs from the last clean
        overworld frame must be covered by the menu. Works for text boxes and full-screen menus alike.
        """
        g = self.game
        if not g.font_loaded():
            self.clean_frame = arr.copy()
            return None
        if self.clean_frame is None:
            return np.ones((18, 20), dtype=bool)
        diff = (arr[:, :, :3] != self.clean_frame[:, :, :3]).any(axis=2)
        return diff.reshape(18, 8, 20, 8).any(axis=(1, 3))

    def _neighbor_shift(self, map_id):
        if not self.wv or map_id is None:
            return None
        key = (self.game.map_id(), map_id)
        cache = self.__dict__.setdefault("_nb_cache", {})
        if key not in cache:
            try:
                cache[key] = self.wv.neighbor_offset(self.pyboy.memory, map_id)
            except Exception:  # noqa: BLE001
                cache[key] = None
            if len(cache) > 64:
                cache.clear()
        return cache[key]

    def draw_friends(self, arr):
        if not self.full:
            return []
        g = self.game
        if not g.in_game() or g.in_battle() or self.battle or self.live:
            self.clean_frame = None
            return []
        mask = self.menu_mask(arr)
        if mask is not None and mask.mean() > 0.85:
            return []  # full-screen menu (party, bag, Pokédex, map...)
        me = g.position()
        tags = []
        layer = self.friend_layer
        layer.fill((0, 0, 0, 0))
        for f in self.friends.values():
            st = f.state
            if not st or not f.sprite or not st.get("visible") or st.get("game", "yellow") != self.game_id \
                    or st.get("challenge") != (self.chal or {}).get("id"):
                continue
            shift = (0, 0)
            if st.get("map") != me["map"]:
                # Friend on a connected map (e.g. Route 1 seen from the top of Pallet Town)?
                shift = self._neighbor_shift(st.get("map"))
                if shift is None:
                    continue
            img = (st.get("facing", 0) & 0xC) | (int(f.anim // 8) % 4 if f.anim else 0)
            sx = sprites.PLAYER_SCREEN_X + int(f.cur_px + shift[0] * 16 - me["px"])
            sy = sprites.PLAYER_SCREEN_Y + int(f.cur_py + shift[1] * 16 - me["py"])
            if -16 - self.off_x < sx < 160 + self.off_x and -16 - self.off_y < sy < 144 + self.off_y:
                surf = self.sprite_cache.get(f.sprite, img, self.palette)
                layer.blit(surf, (sx + self.off_x, sy + self.off_y))
                in_center = 0 <= sy < 144 and 0 <= sx + 8 < 160
                head = (min(17, max(0, sy // 8)), min(19, max(0, (sx + 8) // 8)))
                if mask is None or not in_center or not mask[head]:
                    tags.append((f.name, sx + 8 + self.off_x, sy + self.off_y))
        if mask is not None:
            # Friends stand *behind* menus and text boxes.
            for r, c in zip(*np.nonzero(mask)):
                layer.fill((0, 0, 0, 0), (int(c) * 8 + self.off_x, int(r) * 8 + self.off_y, 8, 8))
        self.game_surf.blit(layer, (0, 0))
        return tags

    def compose_world(self, arr):
        """Fill game_surf: the real 160x144 frame, plus the wide-view margins when enabled."""
        center = pygame.image.frombuffer(arr.tobytes(), (160, 144), "RGBA")
        if not self.wide:
            self.game_surf.blit(center, (0, 0))
            return
        g = self.game
        img = None
        cur_map = g.map_id()
        if self.wv and g.in_game() and not g.in_battle() and not self.battle and not self.live:
            try:
                img = self.wv.render(self.pyboy.memory, arr, fill_center=False)
            except Exception:  # noqa: BLE001
                self._wv_errors += 1
                if self._wv_errors <= 3:
                    log.exception("wide view render failed")
                img = None
            if img is not None:
                self.last_wide = (cur_map, img)
                self._wv_misses = 0
            elif self.last_wide and self.last_wide[0] == cur_map:
                # The renderer refuses single frames (e.g. while the game redraws a row mid-step) and whole
                # stretches while a menu is open. Keep the last good margins instead of flashing black.
                self._wv_misses = getattr(self, "_wv_misses", 0) + 1
                if g.font_loaded() or self._wv_misses <= 12:
                    img = self.last_wide[1]
        if img is not None:
            self.game_surf.blit(pygame.image.frombuffer(img.tobytes(), (self.view_w, self.view_h), "RGBA"), (0, 0))
        else:
            self.game_surf.fill((10, 11, 16))
        self.game_surf.blit(center, (self.off_x, self.off_y))

    def render(self):
        self.click_targets = []
        arr = self.pyboy.screen.ndarray
        self.compose_world(arr)
        tags = self.draw_friends(arr)
        self.screen.fill(ui.BG)
        scaled = pygame.transform.scale(self.game_surf, (self.game_w, self.game_h))
        self.screen.blit(scaled, (0, self.game_y))
        if self.cfg.get("show_names", True):
            for name, cx, top in tags:
                ui.name_tag(self.screen, self.fonts.tag, name, cx * self.scale,
                            self.game_y + top * self.scale - 2)
        sub = self.screen.subsurface((0, self.game_y, self.game_w, self.game_h))
        if self.mode == "panel" and self.panel == "multiplayer":
            panels.draw_multiplayer(sub, self.fonts, self, self.game_w, self.game_h, (0, self.game_y))
        elif self.mode == "panel" and self.panel == "regions":
            panels.draw_regions(sub, self.fonts, self, self.game_w, self.game_h, (0, self.game_y))
        elif self.mode == "panel" and self.panel == "challenges":
            panels.draw_challenges(sub, self.fonts, self, self.game_w, self.game_h, (0, self.game_y))
        elif self.mode == "panel" and self.panel == "soullink":
            panels.draw_soullink(sub, self.fonts, self, self.game_w, self.game_h, (0, self.game_y))
        elif self.mode == "chal_popup" and self.chal_popup:
            p = self.chal_popup
            r = ui.modal(sub, self.fonts, self.game_w, self.game_h, p["title"], p["lines"] + [""],
                         f"[Enter] OK ({max(0, int(p['until'] - time.time()))}s)")
            panels.button(sub, self, self.fonts, pygame.Rect(r.right - 118, r.bottom - 50, 100, 36), "OK",
                          self.chal_close_popup, offset=(0, self.game_y), primary=True)
        elif self.mode == "menu":
            ui.draw_link_menu(sub, self.fonts, self, self.game_w, self.game_h)
        elif self.mode == "trade" and self.trade:
            ui.draw_trade(sub, self.fonts, self, self.game_w, self.game_h)
        elif self.mode == "prompt" and self.prompt:
            left = max(0, int(self.prompt["until"] - time.time()))
            if self.prompt["kind"] == "challenge":
                p = self.prompt
                game = library.GAMES_BY_ID.get(p["game"])
                rules = p.get("rules") or {}
                if p.get("continue"):
                    lines = [f"{p['name']} wants to continue your Soul Link!",
                             (f"{game.title if game else p['game']} - you'll both jump into your run.", ui.DIM)]
                else:
                    lines = [f"{p['name']} challenges you to a Soul Link!",
                             (f"{game.title if game else p['game']}, fresh save (your main game is kept safe).",
                              ui.DIM),
                             ("Dupes clause " + ("on" if rules.get("dupes", True) else "off")
                              + (", shiny clause on" if rules.get("shiny") and p["game"] == "crystal" else ""),
                              ui.DIM)]
                r = ui.modal(sub, self.fonts, self.game_w, self.game_h, "Challenge!", lines + [(f"({left}s)", ui.DIM), ""],
                             "[Y] accept    [N] decline")
                off = (0, self.game_y)
                panels.button(sub, self, self.fonts, pygame.Rect(r.right - 250, r.bottom - 52, 110, 36), "Accept",
                              lambda: self.respond_challenge(True), offset=off, primary=True)
                panels.button(sub, self, self.fonts, pygame.Rect(r.right - 128, r.bottom - 52, 110, 36), "Decline",
                              lambda: self.respond_challenge(False), offset=off)
            elif self.prompt["kind"] == "battle":
                ui.modal(sub, self.fonts, self.game_w, self.game_h, "Battle challenge!",
                         [f"{self.prompt['name']} challenges you to a live battle!",
                          ("Exhibition match: teams fully healed, nothing is lost.", ui.DIM),
                          (f"({left}s)", ui.DIM)],
                         "[Y] accept    [N] decline")
            else:
                ui.modal(sub, self.fonts, self.game_w, self.game_h, "Trade request",
                         [f"{self.prompt['name']} wants to trade with you!", (f"({left}s)", ui.DIM)],
                         "[Y] accept    [N] decline")
        elif self.mode == "battle_confirm" and self.battle_offer:
            o = self.battle_offer
            lines = [f"{self.rom.species_name(m.species)}  Lv {m.level}" for m in o["mons"]]
            lines.append(("Exhibition match: both teams fully healed,", ui.DIM))
            lines.append(("nothing is won or lost.", ui.DIM))
            ui.modal(sub, self.fonts, self.game_w, self.game_h, f"Battle {o['name']}'s team?", lines,
                     "[Enter] fight!    [Esc] back")
        elif self.mode == "help":
            ui.draw_help(sub, self.fonts, self.game_w, self.game_h)
        elif self.mode == "update_confirm":
            ui.modal(sub, self.fonts, self.game_w, self.game_h, f"Update to {self.update_available}?",
                     ["The new version downloads in the background",
                      "while you keep playing. When it's ready you'll",
                      "be asked to restart.", "",
                      ("Tip: save in-game (Start > Save) before restarting.", ui.ACCENT)],
                     "[Enter] download    [Esc] not now")
        elif self.mode == "update_ready":
            ui.modal(sub, self.fonts, self.game_w, self.game_h, "Update ready!",
                     [f"Version {self.update_available} is downloaded.", "",
                      ("SAVE IN-GAME FIRST (Start > Save)!", ui.BAD),
                      ("Anything since your last save is lost on restart.", ui.DIM)],
                     "[Enter] restart now    [Esc] later (press U)")
        if self.chal and self.chal_wait and self.mode != "chal_popup":
            _, partner = self.chal_partner()
            msg = {"load": f"Waiting for {partner} to load in...",
                   "lost": f"{self.chal_wait_name or partner} lost connection - waiting up to 30 s...",
                   "offline": "Connection lost - reconnecting..."}[self.chal_wait]
            band = pygame.Rect(0, self.game_h // 2 - 60, self.game_w, 130)
            shade = pygame.Surface(band.size, pygame.SRCALPHA)
            shade.fill((0, 0, 0, 190))
            sub.blit(shade, band.topleft)
            ui.text(sub, self.fonts.title, msg, (self.game_w // 2, band.y + 22), ui.ACCENT, anchor="midtop")
            ui.text(sub, self.fonts.small, "Paused - a Soul Link only runs while you're both in it.",
                    (self.game_w // 2, band.y + 58), ui.DIM, anchor="midtop")
            panels.button(sub, self, self.fonts, pygame.Rect(self.game_w // 2 - 65, band.y + 84, 130, 34),
                          "Go back", lambda: self.leave_to_main("paused"), offset=(0, self.game_y))
        ui.draw_toasts(sub, self.fonts, self.toasts, self.game_w,
                       bottom=self.mode in ("panel", "chal_popup", "prompt", "trade", "battle_confirm"))
        if self.fast:
            ui.text(sub, self.fonts.body, ">> fast", (self.game_w - 10, 10), ui.ACCENT, anchor="topright")
        ui.draw_sidebar(self.screen, self.fonts, self, self.game_w, self.screen.get_height())
        pygame.display.flip()

    # ----------------------------------------------------------------- loop
    def step(self):
        """Advance one host frame (network, emulation, publishing)."""
        self.handle_net()
        paused = self.chal_paused()
        ticks = 0 if paused else (4 if self.fast and not self.trade else 1)
        for i in range(ticks):
            self.pyboy.tick(1, i == ticks - 1)
            if ticks == 1:
                self.audio.push(self.pyboy.sound.ndarray)
            self.update_battle()
            self.update_live()
        self._try_apply_trade()
        self.chal_step()
        if self.update_dl and self.update_dl.status != "downloading" and not getattr(self.update_dl, "announced", False):
            self.update_dl.announced = True
            if self.update_dl.status == "ready":
                if self.mode == "play":
                    self.mode = "update_ready"
            else:
                self.toast("Update download failed - press U to try again.", 6, ui.BAD)
                self.update_dl = None
        self._maybe_show_prompt()
        if self.unresolved_trade and self.unresolved_retry and time.time() > self.unresolved_retry:
            self.unresolved_retry = 0.0
            self.net.send({"type": "trade_status", "trade_id": self.unresolved_trade[0]})
        if self.prompt and time.time() > self.prompt["until"]:
            if self.mode == "prompt":
                self.mode = "play"
            self.prompt = None
        self.update_friends()
        if self.mode != self._last_mode:
            log.info("mode %s -> %s", self._last_mode, self.mode)
            self._last_mode = self.mode
        if self.frame % 1800 == 0:
            log.info("status: %s", self.debug_snapshot())
        self.frame += 1
        self.publish()
        if self.frame % SRAM_FLUSH_EVERY == 0:
            self.flush_sram()
        if self.frame % AUTOSAVE_EVERY == 0 and not paused:
            self.autosave()

    def run(self):
        next_t = time.perf_counter()
        running = True
        fps_t, fps_n = time.perf_counter(), 0
        try:
            while running:
                try:
                    running = self.handle_events()
                    self.step()
                    self.render()
                except Exception:  # noqa: BLE001
                    self._loop_error()
                fps_n += 1
                if fps_n == 120:
                    now = time.perf_counter()
                    self.fps = 120 / max(1e-6, now - fps_t)
                    fps_t, fps_n = now, 0
                next_t += FRAME_TIME
                delay = next_t - time.perf_counter()
                if delay > 0:
                    time.sleep(delay)
                elif delay < -0.25:
                    next_t = time.perf_counter()
        finally:
            self.shutdown()

    def _loop_error(self):
        """Log and survive occasional errors; give up if they keep happening."""
        log.exception("error in main loop (%s)", self.debug_snapshot())
        now = time.time()
        self._err_times = [t for t in self._err_times if now - t < 5] + [now]
        if len(self._err_times) >= 20:
            raise RuntimeError("too many errors in a row - stopping")
        if now - self._last_err_toast > 10:
            self._last_err_toast = now
            try:
                self.toast("Something went wrong (logged). Press F3 to send a report.", 6, ui.BAD)
            except Exception:  # noqa: BLE001
                pass

    def shutdown(self):
        try:
            if self.battle:
                self._restore_battle()
            if self.live:
                self.net.send({"type": "battle_leave", "battle_id": self.live.battle_id})
                self._end_live("", ui.DIM)
            self.autosave(force=True)
            self.flush_sram(force=True)
            ram = io.BytesIO()
            rtc = io.BytesIO() if self.gen2 else None
            self.pyboy.stop(save=True, ram_file=ram, rtc_file=rtc)
            data = ram.getvalue()
            if len(data) >= 32768 and data != self.last_sram:
                write_atomic(self.cfg.save_path, data)
            if rtc is not None and len(rtc.getvalue()) >= 33:
                write_atomic(self.rtc_path, rtc.getvalue())
        except Exception:  # noqa: BLE001
            log.exception("shutdown save failed")
        self.chal_shutdown()
        self.net.stop()
        pygame.quit()


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    diag = Diagnostics(app_dir())
    updater.cleanup_old()
    cfg = None
    try:
        cfg = Config()
        if diag.previous_crash_log:
            log.warning("previous session did not exit cleanly: %s", diag.previous_crash_log)
            text = diag.file_tail(diag.previous_crash_log, "previous session closed unexpectedly")
            show_report_window(
                text, "YellowLink – it closed unexpectedly last time",
                "YellowLink didn't shut down properly last time (crash, freeze, or closed from Task Manager). "
                "If that wasn't you, please send this log to the host.",
                (lambda: send_report_sync(cfg, text, "previous crash")) if cfg.is_complete() else None,
                diag.dir)
        _main(argv, cfg, diag)
    except Exception as e:  # noqa: BLE001
        log.exception("fatal error")
        text = diag.report_text(f"crash: {e.__class__.__name__}: {e}")
        show_report_window(
            text, "YellowLink crashed",
            "Sorry - YellowLink crashed. Your last in-game save is safe. Please click \"Send to host\" "
            "(or Copy and paste it to the host) and say what you were doing.",
            (lambda: send_report_sync(cfg, text, "crash")) if cfg and cfg.is_complete() else None,
            diag.dir)
    finally:
        diag.clean_exit()


def _main(argv, cfg, diag):
    if "--setup" in argv or not cfg.is_complete():
        from .setup_dialog import run_setup
        if not run_setup(cfg, first_run=not cfg.is_complete()):
            return
    if cfg.pop("active_challenge", None):      # crashed/closed inside a challenge: start in the main game
        cfg.save()
    while True:
        errors_before = diag.errors
        app = App(cfg, diag)
        app.run()
        if app.enter_challenge:
            cfg["active_challenge"] = app.enter_challenge
            cfg.save()
            continue
        if app.leave_challenge:
            cfg.pop("active_challenge", None)
            cfg.save()
            if app.travel_to:
                library.Library(cfg, app_dir()).set_current(app.travel_to)
            continue
        if app.travel_to:
            library.Library(cfg, app_dir()).set_current(app.travel_to)
            continue
        if app.restart_into:
            try:
                updater.install_and_restart(app.restart_into)
                return
            except Exception:  # noqa: BLE001
                log.exception("self-update failed; opening the download page instead")
                webbrowser.open(app.download_url or "https://github.com")
                return
        if diag.errors > errors_before:
            text = diag.report_text(f"{diag.errors - errors_before} error(s) during the session")
            show_report_window(
                text, "YellowLink – problems during this session",
                "Some errors happened while you were playing (the game kept running). "
                "Please send this to the host so they can be fixed.",
                lambda: send_report_sync(cfg, text, "session errors"), diag.dir)
        if cfg.pop("_open_setup", False):
            cfg.save()
            from .setup_dialog import run_setup
            if run_setup(cfg, first_run=False):
                continue
        break
