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

from . import __version__, game, library, nds_core, panels, pcdb, pvp, sprites, ui, updater, worldview
from .audio import AudioOut
from .config import Config, app_dir
from .diag import Diagnostics, send_report_sync, show_report_window
from .game import GameMemory
from . import crystal
from .challenge import ChallengeMixin
from .tradeevo import TradeEvoMixin
from .trades import TradeMixin
from .badgesync import BadgeMixin
from .shiny import ShinyMixin
from . import badge_art
from .net import NetClient

try:                                   # Pokémon Platinum adapter (dev/platinum/BRIEF.md); optional while it's built
    from . import platinum
except ImportError:
    platinum = None
try:                                   # Platinum's PC boxes as a view of the PC database (Gen4PC)
    from . import platinum_pc
except ImportError:
    platinum_pc = None
from . import sinnoh_view                # friends walking around in Platinum's 3D overworld

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
# Nintendo DS (Platinum): the Game Boy keys plus X / Y / L / R
DS_KEYMAP = {**KEYMAP, pygame.K_d: "x", pygame.K_c: "y", pygame.K_q: "l", pygame.K_w: "r"}
DS_PAD_BUTTONS = {**PAD_BUTTONS, 2: "x", 3: "y", 4: "l", 5: "r"}
DS_FAST_TICKS = 4           # DS frames per host frame while fast-forward is on
# Game Boy pixels: the "Shiny!" tag sits centred under the wild enemy's HUD (its HP bar's frame ends at y 28),
# above the player's Pokémon (back picture from y 40) and clear of the player's name/level HUD (y 56+)
SHINY_TAG_X, SHINY_TAG_Y = 48, 30
# Game Boy pixels: Yellow's "already caught" ball sits in tile (1, 1) - under the start of the enemy's name, left of its
# level (blank in Yellow's enemy HUD; the spot Crystal uses for its own indicator). SPEC.md section 10
OWNED_BALL_X, OWNED_BALL_Y = 8, 8
DS_SAVE_POLL = 180          # frames (~3 s) between battery save checks (Platinum)


class _NoPlatinumMemory:
    """Stand-in until client/yellowlink/platinum.py exists: never in game (no multiplayer state, nothing written)."""

    def __init__(self, bus):
        self.bus = bus

    def in_game(self):
        return False

    def is_safe(self):
        return False

    def in_battle(self):
        return False

    def walking(self):
        return False

    def map_id(self):
        return None

    def position(self):
        return {}

    def status_line(self):
        return "platinum adapter not available"


def _platinum_adapter():
    """The Platinum adapter module (self.G), or a stand-in with the same names if platinum.py isn't there yet."""
    if platinum is not None and hasattr(platinum, "PlatinumMemory"):
        return platinum
    import types
    return types.SimpleNamespace(PlatinumMemory=_NoPlatinumMemory, fast_continue=None, RomData=None,
                                 PLATINUM_SHA1=None)


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
class GhostBattle:
    opponent_uid: str
    opponent_name: str
    mons: list
    phase: str = "waiting"       # waiting | triggered | running | ending
    snapshot: bytes = b""
    started: float = field(default_factory=time.time)
    end_frames: int = 0
    result: int | None = None


class App(ChallengeMixin, TradeMixin, BadgeMixin, TradeEvoMixin, ShinyMixin):
    def __init__(self, cfg: Config, diag: Diagnostics | None = None):
        self.cfg = cfg
        self.diag = diag
        self.lib = library.Library(cfg, app_dir())
        if cfg.challenge and cfg.challenge["game"] not in self.lib.games:
            log.warning("challenge game %s is not installed; back to the main game", cfg.challenge["game"])
            cfg.pop("active_challenge", None)
            cfg.save()
        self.init_challenges()
        self.init_trades()
        self.init_badges()
        self.init_tradeevo()
        self.init_shiny()
        # in a challenge: that run's own game + separate save (Config paths follow active_challenge)
        self.game_id = cfg.challenge["game"] if cfg.challenge else (self.lib.current or "yellow")
        self.save_tag = f"challenge-{cfg.challenge['id']}" if cfg.challenge else self.game_id
        self.game_info = self.lib.info(self.game_id)
        self.travel_to = None
        # per server (each group of friends has its own) - or shared by servers that share one save (SPEC.md 8)
        self.pcsync = pcdb.PCSync(cfg.pc_path, *cfg.pc_keys)
        self.pc_ready = not cfg.save_owner(cfg.active_server)   # shared save: no trades until synced here
        self.switch_to = None        # set -> main loop restarts on that server
        self._confirm_remove = None
        self.pc_codec = None
        self.exp_share = None        # handle of the game's Exp Share All hooks (see _install_exp_share)
        self.pc_trust = False        # True once the game's boxes are known to match our last view
        self.panel = None            # None | "multiplayer" | "regions"
        self.panel_sel = None        # selected uid in the multiplayer panel
        self.click_targets = []      # [(screen Rect, callback, label)] rebuilt every frame
        self._last_mode = "play"
        self._err_times: list = []
        self._last_err_toast = 0.0
        self.fps = 0.0
        cfg["rom_path"] = self.lib.rom_path(self.game_id) or cfg.get("rom_path", "")
        # One adapter module per game: game (Yellow, Gen 1), crystal (Gen 2) or platinum (Gen 4, Nintendo DS).
        # Same method names. The DS game runs in nds_core.NDSCore (DeSmuME) instead of PyBoy.
        self.ds = self.game_info.platform == "nds"
        self.gen2 = self.game_id == "crystal"
        self.G = _platinum_adapter() if self.ds else (crystal if self.gen2 else game)
        rom_cls = getattr(self.G, "RomData", None)
        self.rom = rom_cls.from_file(cfg["rom_path"]) if rom_cls else None
        # Multiplayer features rely on memory addresses verified on these exact ROMs.
        # Live/practice battles and wide view are Yellow-only (self.hooks / self.wv).
        sha = library.sha1_file(cfg["rom_path"])
        if self.ds:
            self.full = (self.game_info.full_support and sha == library.PLATINUM_SHA1
                         and getattr(self.G, "PLATINUM_SHA1", None) == library.PLATINUM_SHA1)
        else:
            self.full = self.game_info.full_support and sha in (game.YELLOW_SHA1, crystal.CRYSTAL_SHA1)
        self.keymap = DS_KEYMAP if self.ds else KEYMAP
        self.pad_buttons = DS_PAD_BUTTONS if self.ds else PAD_BUTTONS
        self.frame_time = nds_core.FRAME_TIME if self.ds else FRAME_TIME
        self.pyboy = None            # Game Boy games (PyBoy)
        self.nds: nds_core.NDSCore | None = None    # Nintendo DS (Platinum)
        self.emu = None              # whichever of the two runs this game (press/release, save/load state, tick)
        self.touching = False        # mouse button held on the DS touch screen
        self._touch_frames = 0
        self._touch_release = False
        self._bat_seen = None        # DS battery save at the last check (written once it stops changing)
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
        self.prompt: dict | None = None
        self.battle_offer: dict | None = None
        self.battle: GhostBattle | None = None
        self.live: pvp.LiveBattle | None = None
        self.update_dl: updater.Download | None = None
        self.restart_into = None
        self.quit_requested = False
        self.hooks: pvp.LinkHooks | None = None
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

        try:
            self._start_emulator()
        except BaseException as e:
            # don't leave our sound device (or the shared DS emulator) open behind an App that never ran
            self.audio.close()
            if self.nds is not None:
                self.nds.close(blank_battery=False)
            if self.ds and isinstance(e, Exception) and not isinstance(e, nds_core.DSUnavailable):
                raise nds_core.DSUnavailable(f"Pokémon Platinum could not start: {e}") from e
            raise
        self.net = NetClient(cfg["server_url"], {
            "name": cfg["name"], "uid": cfg["uid"], "key": cfg.get("room_key", ""), "version": __version__})
        self.net.start()
        self.sys_msg(f"Welcome, {cfg['name']}! Press F1 for help, F3 to report a problem.")
        ds_error = cfg.pop("_ds_error", None)
        if ds_error:
            self.sys_msg(f"Pokémon Platinum couldn't start ({ds_error}). Press F3 to send a report.", ui.BAD)
            self.toast("Pokémon Platinum couldn't start - press F3 to send a report.", 8, ui.BAD)
        log.info("config: name=%s server=%s rom=%s", cfg["name"], cfg["server_url"], cfg["rom_path"])
        if not cfg.get("seen_help"):
            self.mode = "help"  # show the controls once on first launch
            cfg["seen_help"] = True
            cfg.save()

    # ------------------------------------------------------------------ setup
    def _make_window(self):
        if self.wide and not self.ds:
            self.off_x, self.off_y = WIDE_MX, WIDE_MY
        else:
            self.off_x, self.off_y = 0, 0
        if self.ds:                              # two DS screens, the touch screen below
            self.view_w, self.view_h = nds_core.SCREEN_W, 2 * nds_core.SCREEN_H
        else:
            self.view_w, self.view_h = 160 + 2 * self.off_x, 144 + 2 * self.off_y
        if self.ds:      # the DS view is tall: half steps (1.5x) so it still fits laptops / 125 % Windows scaling
            self.scale = (round(float(self.cfg.get("ds_scale", 0) or 0) * 2) / 2) or self._fit_scale()
        else:
            self.scale = int(self.cfg.get(self._scale_key(), 0) or 0) or self._fit_scale()
        self.game_w, self.game_h = int(self.view_w * self.scale), int(self.view_h * self.scale)
        h = max(self.game_h, 480)
        self.screen = pygame.display.set_mode((self.game_w + ui.SIDEBAR_W, h))
        self.game_surf = pygame.Surface((self.view_w, self.view_h))
        self.friend_layer = pygame.Surface((self.view_w, self.view_h), pygame.SRCALPHA)
        self.game_y = (h - self.game_h) // 2

    def _scale_key(self):
        return "ds_scale" if self.ds else ("wide_scale" if self.wide else "scale")

    def _fit_scale(self):
        """Largest integer scale that fits the desktop (with the sidebar)."""
        try:
            sizes = pygame.display.get_desktop_sizes()
            dw, dh = sizes[0] if sizes else (1920, 1080)
        except (pygame.error, AttributeError):
            dw, dh = 1920, 1080
        if self.ds:
            fit = int(min((dw - ui.SIDEBAR_W - 40) / self.view_w, (dh - 100) / self.view_h) * 2) / 2
            return max(1.0, min(4.0, fit))
        fit = min((dw - ui.SIDEBAR_W - 40) // self.view_w, (dh - 100) // self.view_h)
        return max(1, min(4, fit))

    def _start_emulator(self):
        if self.ds:
            self._start_nds()
            return
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
        self.emu = self.pyboy
        self.game = (crystal.CrystalMemory if self.gen2 else GameMemory)(self.pyboy.memory, self.rom)
        # Hooks patch ROM addresses, so only enable them on the exact ROM they were verified on.
        if self.full and self.gen2:
            self.pc_codec = None if self.chal else crystal.Gen2PC(self.pyboy.memory, self.rom)
            if self.pc_codec:
                self.pc_codec.plat_rom = self.plat_data      # Platinum mons convert (else ?NAME stand-ins)
            self.pyboy.hook_register(*crystal.HOOK_PC_OPEN, self._hook_safe(self.on_pc_open), None)
            self.pyboy.hook_register(*crystal.HOOK_PC_CLOSE, self._hook_safe(self.on_pc_close), None)
            if self.wide:
                self.wide = False
                self._make_window()
        elif self.full:
            self.hooks = pvp.LinkHooks(self.pyboy, self._send_battle_move)
            self.wv = worldview.WorldView(self.rom.rom, margin_x=WIDE_MX, margin_y=WIDE_MY)
            self.pc_codec = None if self.chal else pcdb.Gen1PC(self.pyboy.memory, self.rom)
            if self.pc_codec:
                self.pc_codec.plat_rom = self.plat_data
            self.pyboy.hook_register(0x08, 0x546F, self._hook_safe(self.on_pc_open), None)    # BillsPC_
            self.pyboy.hook_register(0x08, 0x553E, self._hook_safe(self.on_pc_close), None)   # ExitBillsPC
        else:
            log.info("%s: multiplayer features, live battles and wide view are Yellow-only for now", self.game_id)
            if self.wide:
                self.wide = False
                self._make_window()
        self.last_sram = self._read_sram()
        self._install_exp_share()
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

    def _start_nds(self):
        """Pokémon Platinum: DeSmuME with this server's own battery save (never DeSmuME's shared .dsv)."""
        msg = self.cfg.pop("_travel_msg", None)
        if msg:
            self._loading_screen(msg)
        save = self.cfg.save_path
        data = b""
        if save.exists():
            try:
                data = save.read_bytes()[:nds_core.SAVE_SIZE]
            except OSError:
                log.exception("could not read %s", save)
        self.had_save = len(data) == nds_core.SAVE_SIZE and data.count(0xFF) + data.count(0) < len(data)
        if save.exists():
            shutil.copy2(save, self.cfg.backup_dir / time.strftime(f"{self.save_tag}-%Y%m%d-%H%M%S.sav"))
            self._prune_backups()
        self.nds = nds_core.NDSCore(self.cfg["rom_path"], save if len(data) == nds_core.SAVE_SIZE else None,
                                    volume=self._ds_volume())
        self.emu = self.nds
        log.info("DS game %s started (save %s, %s)", self.game_id, save, "loaded" if self.nds.loaded_save else "none")
        self.game = self.G.PlatinumMemory(self.nds)
        if self.chal and self.full and hasattr(self.game, "rom"):
            self.game.rom = self.plat_data       # Soul Link's forced PC moves need the ROM (move PP, stats)
        self._start_pt_pc()
        self._install_exp_share()
        if self.wide:
            self.wide = False
        self.last_sram = self._read_sram()
        self._bat_seen = self.last_sram
        dev_state = os.environ.get("YL_DEV_LOAD_STATE")
        if dev_state:
            self.nds.load_state(dev_state)
            self.last_sram = self._bat_seen = self._read_sram()
        elif self._try_resume():
            pass
        elif self.full and self.had_save and self.cfg.get("skip_intro", True) and \
                getattr(self.G, "fast_continue", None):
            self._loading_screen("Loading your adventure...")
            t0 = time.time()
            try:
                result = self.G.fast_continue(self.nds, on_progress=lambda: self._loading_screen(
                    "Loading your adventure..."))
            except Exception:  # noqa: BLE001 - a failed skip must never stop the game: play the intro instead
                log.exception("fast continue failed")
                result = "failed"
            log.info("fast continue: %s in %.2fs", result, time.time() - t0)

    def _ds_volume(self) -> int:
        """What DeSmuME's own sound device should play at: muted in fast-forward (like the Game Boy games)."""
        audio = getattr(self, "audio", None)
        if audio is None:
            return int(self.cfg.get("volume", 60))
        if audio.muted or self.fast:
            return 0
        return int(round(audio.volume * 100))

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
                self.emu.load_state(f)
            self.last_sram = self._read_sram()
            self._bat_seen = self.last_sram
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
        if self.battle or self.live:
            return False
        if self._exp_award_busy():
            # the game is in the middle of handing out experience (hook state lives in Python): resuming from
            # here would give wrong amounts - keep the last snapshot, the game resumes from before this battle
            log.info("no snapshot: experience is being handed out right now")
            return False
        if self.pcsync.session_open:
            # never snapshot inside Bill's PC: the store's view of the game would no longer match on resume
            return False
        if self.full and not force and not self.game.is_safe():
            return False
        buf = io.BytesIO()
        try:
            self.emu.save_state(buf)
        except Exception:  # noqa: BLE001
            log.exception("snapshot failed")
            return False
        ok = write_atomic(self.cfg.resume_path, buf.getvalue())
        if ok:
            self.flush_sram()
            if not self.chal:
                store = self.pcsync.commit()
                merged = self.pcsync.take_deferred()
                if store or merged:
                    self.pc_push()
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

    def _read_sram(self) -> bytes | None:
        if self.ds:
            return self.nds.export_save()            # the 512 KB battery save
        m = self.pyboy.memory
        return b"".join(bytes(m[b, 0xA000:0xBFFF]) + bytes([m[b, 0xBFFF]]) for b in range(4))

    def flush_sram(self, force=False):
        if (self.battle or self.live) and not force:
            return
        data = self._read_sram()
        if data is None:
            return
        if self.ds and not force and data != self._bat_seen:
            # Platinum writes its save over a second or two: only keep it once it stopped changing
            self._bat_seen = data
            return
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
                f"trade={self.trade.trade_id if self.trade else None} "
                f"battle={self.battle.phase if self.battle else None} "
                f"live={(self.live.phase, self.live.exchange) if self.live else None} friends={len(self.friends)} | {gs}")

    def open_report(self, reason="player report (F3)"):
        self.release_all()
        if not self.diag:
            return
        self._pause_ds_audio(True)
        try:
            self._open_report(reason)
        finally:
            self._pause_ds_audio(False)

    def _open_report(self, reason):
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
        if self.cfg.save_owner(self.cfg.active_server):
            self.pc_ready = False          # shared save: trade only once this server has our current PC
        if not self.chal:
            self.net.send({"type": "pc_get"})
            for tid in self.pcsync.locks_here():
                self.net.send({"type": "trade_status", "trade_id": tid})
        self.chal_on_connected()
        self.last_state_sent = None
        self.last_sprite_sent = None
        self.last_party_sent = None

    def on_disconnected(self, msg):
        self.chal_on_disconnected()
        log.warning("disconnected: %s (%s)", self.net.status, msg.get("reason"))
        if self.live:
            self._end_live("Battle ended: connection lost.", ui.BAD)
        self.friends.clear()
        if self.trade:
            # trades happen on the server in one step: if both had confirmed it may have gone through; the
            # offered Pokémon stays locked until we know (asked again on reconnect)
            self._end_trade("Connection lost - if you'd both confirmed, the trade may still have gone through "
                            "(it syncs when you're back).", ui.ACCENT)
        if self.net.status == "rejected":
            self.sys_msg("Server refused connection: " + self.net.status_detail, ui.BAD)

    def on_moved(self, msg):
        """This server was merged into another one: take over its address + password (same saves, same player
        id) and reconnect there. SPEC.md section 8."""
        url, key = str(msg.get("url") or ""), str(msg.get("key") or "")
        sid = self.cfg.server_id
        try:
            twin = self.cfg.move_server(sid, url, key)
        except ValueError:
            log.warning("ignoring a bad 'moved' message: %r", msg)
            return
        log.info("server moved to %s (twin entry: %s)", url, twin)
        name = self.cfg.server_label(self.cfg.active_server)
        if self.chal or self.trade or self.battle or self.live:
            self.sys_msg(f"This server moved - reconnecting to {name}...", ui.ACCENT)
        self._restart_on(sid, "This server moved - reconnecting...")

    def on_welcome(self, msg):
        self.server_name = str(msg.get("server_name") or "")[:30]
        entry = self.cfg.server(self.cfg.server_id)
        if entry is not None and entry.get("seen_name", "") != self.server_name:
            entry["seen_name"] = self.server_name          # shown in the server list when you didn't name it
            self.cfg.save()
        self.sys_msg(f"Connected to {self.server_name}." if self.server_name else "Connected to server.", ui.GOOD)
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
        self.server_features = set(msg.get("features") or [])
        self.badges_on_welcome(msg)

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
        self.shiny_on_system(msg)          # a shiny alert of someone else: banner + jingle (SPEC.md section 10)

    def on_roster(self, msg):
        self.roster = msg.get("roster", [])
        self.badges_on_roster()

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

    def _maybe_show_prompt(self):
        if self.prompt and self.mode == "play" and self.prompt["shown"] is None:
            if self.prompt["kind"] == "tradeevo" and not self.evo_prompt_ready():
                return                     # the partner's question waits for a quiet moment (not mid-battle)
            self.prompt["shown"] = time.time()
            self.mode = "prompt"
            self.release_all()

    # battles ------------------------------------------------------------
    def on_party_data(self, msg):
        if not self.full or self.gen2 or self.ds:
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
            self.emu.load_state(io.BytesIO(b.snapshot))
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
            self.emu.load_state(io.BytesIO(s.snapshot))
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
        if self.ds:
            self._publish_ds()
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

    def _publish_ds(self):
        """Platinum: where we are (PlatinumMemory.position()), for the roster/region and friends in Sinnoh."""
        if self.frame % STATE_SEND_EVERY:
            return
        pos = self.game.position() or {}
        try:
            x, y = int(pos.get("x", 0)), int(pos.get("y", 0))
            st = {k: v for k, v in pos.items() if isinstance(v, (int, float, str, bool)) and not isinstance(v, float)}
            st.update({"map": int(pos.get("map", 0)), "x": x, "y": y,
                       "px": int(pos.get("px", x * 16)), "py": int(pos.get("py", y * 16)),
                       "facing": int(pos.get("facing", 0)),
                       # gender/running/bike/height for friends' screens (SPEC.md 2; the server keeps only img)
                       "img": sinnoh_view.pack_img(pos.get("gender", 0), pos.get("running"), pos.get("state", 0),
                                                   pos.get("ph", 0))})
        except (TypeError, ValueError):
            return
        st.update(type="state", visible=True, game=self.game_id)
        if self.chal:
            st["challenge"] = self.chal["id"]     # only players in the same run draw us (and we them)
        now = time.time()
        if st != self.last_state_sent or now - self.last_state_time > 2:
            self.net.send(st)
            self.last_state_sent, self.last_state_time = st, now

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
        if self.ds and (not self.pc_codec.initialised() or self.game.pc_open()):
            log.warning("Platinum PC open hook outside the game / under a running box UI: not syncing")
            return
        boxes = self.pcsync.open_session(self.game_id, self.pc_codec, trust_game=self.pc_trust)
        if boxes is None:                # store too full to link this game: its boxes stay exactly as they are
            n, free = self.pcsync.refused or (0, 0)
            game = self.game_info.title.replace("Pokémon ", "")
            self.toast(f"Your YellowLink PC only has room for {free} more Pokémon, but {game}'s PC has {n}: it can't "
                       "be linked yet and nothing was changed. Make room in another game, then open the PC again.",
                       10, ui.BAD)
            return
        try:
            self.pc_codec.write_boxes(boxes)     # computes everything first: raises before writing anything
        except ValueError:
            log.exception("PC view doesn't fit the game; not syncing this visit")
            self.pcsync.abort_open()
            self.toast("Your PC boxes couldn't be shown in this game (too many Pokémon) - nothing was changed.",
                       10, ui.BAD)
            return
        self.pc_trust = True
        self._pc_mark_dex(boxes)
        if self.ds:
            self._pt_pc_report(opening=True)
        log.info("PC opened: %d mons in the database", self.pcsync.count())

    def _pc_mark_dex(self, boxes):
        """Badges (0.7.5): the mons YellowLink just put into this game's PC view (trades, gifts, the player's other
        games) count in its Pokédex like a received trade does in the real game (codec.mark_dex: seen + caught;
        Crystal Unown letters; Platinum Pokedex_Capture). Main game only - pc_codec is None inside a challenge."""
        mark = getattr(self.pc_codec, "mark_dex", None)
        if mark is None:
            return
        try:
            new = mark(boxes)
        except Exception:  # noqa: BLE001 - the dex is a bonus; never break the PC sync over it
            log.exception("marking the Pokédex for the PC view failed")
            return
        if new:
            log.info("Pokédex: %d species now caught through YellowLink's PC: %s", len(new), new)

    def on_pc_close(self, party=None):
        if self.chal:
            if self.referee:
                self.referee.on_pc_close()
            return
        if not self.pc_codec or not self.pcsync.session_open:
            return
        if party is None:
            party = self.G.party_records(self.game)
        report = self.pcsync.close_session(self.game_id, self.pc_codec, party)
        self.badges_due()        # count a shiny just deposited on the next safe frame (before it can be traded)
        if self.ds:
            # Platinum: party mons are taken out (by their exact bytes) and items given back once the game isn't
            # in the middle of updating the party (_pt_fix_party)
            codec = self.pc_codec
            fix = {"remove": [], "give": list(report.get("items_back", [])),
                   "take": list(report.get("items_taken", [])), "until": self.frame + 600}
            for slot, old in report.get("party_removals", {}).items():
                # the item changes only count once that party mon is really gone (_pt_fix_party)
                give, take = codec.item_changes(old, party[slot]) if old is not None else \
                    ([i for i in [codec.held_item(party[slot])] if i], [])
                fix["remove"].append((party[slot], give, take))
            if fix["remove"] or fix["give"] or fix["take"]:
                old_fix = self._pt_party_fix          # one still waiting (hook path): keep it, add ours
                if old_fix:
                    for k in ("remove", "give", "take"):
                        fix[k] = old_fix[k] + fix[k]
                self._pt_party_fix = fix
            self._pt_pc_report()
        else:
            for slot in sorted(report["standin_party_slots"], reverse=True):
                self.game.remove_party_slot(slot)
        if report.get("locked_returned"):
            names = ", ".join(n.title() for n in report["locked_returned"][:3])
            self.toast(f"{names} is part of a trade that's still finishing - it stays in the PC.", 6, ui.ACCENT)
        if report.get("traded_returned"):
            self.toast("A Pokémon you traded away can't be withdrawn - it's with your friend now.", 6, ui.ACCENT)
        if report["standins_returned"]:
            names = ", ".join(n.title() for n in report["standins_returned"][:3])
            self.toast(f"{names} can't leave the PC here - they live in another region.", 6, ui.ACCENT)
        log.info("PC closed: +%d -%d (pending=%s)", report["added"], report["removed"], self.pcsync.pending)

    # Platinum: the box UI is opened through an exec hook and its closing is polled (dev/platinum/MEMORY_MAP.md)
    @property
    def plat_data(self):
        """The player's Platinum ROM data (ndsrom.PlatinumData: species names, growth, Gen-4 conversions) or None."""
        if not hasattr(self, "_plat_data"):
            path = self.cfg["rom_path"] if self.ds else self.lib.rom_path("platinum")
            self._plat_data = platinum_pc.load_data(path) if platinum_pc and path else None
        return self._plat_data

    def _start_pt_pc(self):
        self._pt_pc = None               # None | "opening" (hook fired) | "open" (box UI running)
        self._pt_pc_wait = 0
        self._pt_party_fix = None        # (party records to take out, items to give back, give-up frame)
        self._pt_close_wait = 0
        self._pt_close_prev = None
        if not (self.full and platinum_pc and not self.chal and hasattr(self.G, "HOOK_PC_OPEN")):
            return
        data = self.plat_data
        if data is None:
            log.warning("no Platinum ROM data: the PC database is off in Platinum")
            return
        self.pc_codec = platinum_pc.Gen4PC(self.game, data, platinum_pc.load_item_pockets(self.cfg["rom_path"]))
        self.nds.hook_register(self.G.HOOK_PC_OPEN, self._pt_pc_hook)

    def _pt_pc_hook(self):
        """FieldSystem_OpenPokemonStorage: the box UI is about to start and hasn't read the boxes yet."""
        if self.pcsync.session_open:     # a close we didn't see / didn't finish: harvest it first
            self.on_pc_close(self._pt_party_now())
        self._pt_pc, self._pt_pc_wait = "opening", 0
        self.on_pc_open()

    def _pt_pc_poll(self, frames):
        """Every host frame: notice the box UI closing (pc_open() goes False) -> on_pc_close."""
        if self._pt_pc is None:
            if self._pt_party_fix:
                self._pt_fix_party()
            return
        now_open = self.game.pc_open()
        if self._pt_pc == "opening":
            self._pt_pc_wait += frames
            if now_open:
                self._pt_pc = "open"
            elif self._pt_pc_wait > 600:     # the UI never came up (10 s): end the session anyway
                log.warning("Platinum box UI didn't open after the hook; closing the PC session")
                self._pt_pc = None
                self.on_pc_close(self._pt_party_now())
        elif self._pt_pc == "open":
            if not now_open:
                self._pt_pc, self._pt_close_wait, self._pt_close_prev = "closing", 0, None
                self._pt_pc_poll(0)
        elif self._pt_pc == "closing":
            # Harvest at a settled moment: the game decrypts records in place while it works on them and a DS frame
            # can end in the middle of that -> wait until the party reads clean (party_checked) and the boxes read
            # the same on two frames in a row (normally the next frame).
            party = self.game.party_checked()
            addr = self.game.box_slot_addr(0, 0)
            slots = self.game.read(addr, platinum_pc.ALL_SLOTS * 136) if addr is not None else None
            self._pt_close_wait += frames
            if slots is None:                # the save left memory (soft reset?): the game state is gone
                if self._pt_close_wait > 120:
                    log.warning("Platinum PC closed without game data in memory; dropping the PC session")
                    self._pt_pc = None
                    self.pcsync.rollback()
                return
            if (party is not None and slots == self._pt_close_prev) or self._pt_close_wait > 120:
                if party is None:
                    log.warning("Platinum party didn't settle after the PC closed; harvesting anyway")
                self._pt_pc = None
                self.on_pc_close([r[:136] for r in party] if party is not None else None)
            else:
                self._pt_close_prev = slots

    def _pt_party_now(self):
        party = self.game.party_checked()
        return [r[:136] for r in party] if party is not None else None

    def _pt_fix_party(self):
        """After the PC closed: take stand-ins (and traded-away / locked mons) back out of Platinum's party and set
        the bag right (items the box UI moved between the bag and a mon whose stored copy we keep). The game
        decrypts party records in place while it works on them and a DS frame can end in the middle of that, so
        this waits for a moment where the party reads clean (party_checked). Item changes of a party mon count
        only if that mon was really taken out; one that can't be (the last party mon) is retried until `until`."""
        fix = self._pt_party_fix
        recs = self.game.party_checked()
        if recs is None:
            if self.frame > fix["until"]:
                log.warning("Platinum party never settled; %d Pokémon left in the party", len(fix["remove"]))
                self._pt_party_fix = None
                self._pt_bag(fix["give"], fix["take"])
            return
        codec = self.pc_codec
        targets = list(fix["remove"])
        for r in recs:                       # any stand-in in the party (one we didn't list, e.g. from a crash)
            if codec.is_standin(r[:136]) and not any(t[0] == r[:136] for t in targets):
                targets.append((r[:136], [i for i in [codec.held_item(r)] if i], []))
        slots = {}
        for t in targets:                    # exact bytes: a look-alike is never taken
            i = next((i for i, r in enumerate(recs) if i not in slots and r[:136] == t[0]), None)
            if i is not None:
                slots[i] = t
        give, take, left = list(fix["give"]), list(fix["take"]), []
        for i in sorted(slots, reverse=True):
            if codec.remove_party_slot(i):
                give += slots[i][1]
                take += slots[i][2]
            else:
                left.append(slots[i])
        if left and self.frame <= fix["until"]:
            self._pt_party_fix = {"remove": left, "give": [], "take": [], "until": fix["until"]}
        else:
            if left:
                log.warning("could not take %d Pokémon out of the Platinum party (last one?)", len(left))
            self._pt_party_fix = None
        if len(slots) > len(left):
            log.info("took %d Pokémon back out of the Platinum party", len(slots) - len(left))
        self._pt_bag(give, take)

    def _pt_bag(self, give, take):
        codec = self.pc_codec
        name = (lambda i: self.plat_data.item_name(i)) if self.plat_data is not None else (lambda i: f"item {i}")
        not_taken = [i for i in take if not codec.take_item(i)]
        if not_taken:
            log.warning("items %s should leave the bag (they stay with the stored Pokémon) but aren't there",
                        not_taken)
        lost = [i for i in give if not codec.give_item(i)]
        back = [i for i in give if i not in lost]
        if back:
            self.toast(f"{', '.join(name(i) for i in back[:3])} went back to your bag (that Pokémon stays as it "
                       "was in the PC).", 7, ui.ACCENT)
        if take and len(not_taken) < len(take):
            self.toast(f"{', '.join(name(i) for i in take[:3] if i not in not_taken)} stays with the Pokémon in "
                       "the PC (it can't be changed here).", 7, ui.ACCENT)
        if lost:
            log.warning("could not return items %s: bag pocket full", lost)
            self.toast(f"Your bag is full: couldn't put back {', '.join(name(i) for i in lost[:3])}.", 8, ui.BAD)

    def _pt_pc_report(self, opening=False):
        codec = self.pc_codec
        left = sum(1 for r in getattr(codec, "unplaced", []) if platinum_pc.classify(r) == "mon")
        if left:
            self.toast(f"Your YellowLink PC is full: {left} Pokémon stay in Platinum's boxes 15-18 until you make "
                       "room.", 8, ui.BAD)
        elif getattr(codec, "harvested", 0) and not opening:
            self.toast("Platinum shows your YellowLink PC in boxes 1-14 (20 per box): Pokémon put elsewhere move to "
                       "free spaces there.", 7, ui.ACCENT)

    def pc_push(self):
        """Compare-and-swap upload of the committed PC store (SPEC.md section 5)."""
        if self.chal:
            return
        up = self.pcsync.upload()
        if up:
            store, base = up
            self.net.send({"type": "pc_put", "store": store, "base": base})

    def on_pc_data(self, msg):
        store = msg.get("store")
        if store is None:
            self.pcsync.server_rev = 0          # the server has nothing (new player / new server): give it ours
            self.pc_push()
            return
        if int(store.get("srv_rev", 0)) != self.pcsync.server_rev:
            if self.pcsync.merge_remote(store):
                self.sys_msg("Your PC boxes were synced with the server.", ui.GOOD)
            else:
                return                          # merged right after the next commit
        self.pc_push()

    def on_pc_conflict(self, msg):
        store = msg.get("store")
        if not store:
            self.pcsync.server_rev = 0
            self.pc_push()
        elif self.pcsync.merge_remote(store):
            self.pc_push()
        else:
            log.info("PC store conflict: merging after the next commit")

    def on_pc_saved(self, msg):
        self.pcsync.saved(msg.get("srv_rev", 0))
        self.pc_ready = True
        log.info("PC store saved on the server (srv_rev %s)", msg.get("srv_rev"))

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
        self.evo_pick = False
        if self.mode == "panel":
            self.mode = "play"

    def select_player(self, uid):
        self.panel_sel = None if self.panel_sel == uid else uid
        if self.panel_sel:
            self.request_badges(uid)          # earn dates for the badge hover text

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

    # servers (one per group of friends) ------------------------------------------------------------------------
    def switch_server(self, sid):
        """Save, close and reopen on another server: its own saves, PC and challenges (see Config)."""
        entry = self.cfg.server(sid)
        if entry is None or sid == self.cfg.server_id:
            return
        if self.chal or self.enter_challenge:
            self.toast("Pause your Soul Link first (Soul Link > Pause & go back).", 5, ui.BAD)
            return
        if self.trade or self.battle or self.live:
            self.toast("Finish your trade or battle first.", 4, ui.BAD)
            return
        self._restart_on(sid, f"Switching to {self.cfg.server_label(entry)}...")

    def _restart_on(self, sid, msg):
        log.info("restarting on server %s (%s)", sid, (self.cfg.server(sid) or {}).get("url"))
        self.cfg["_travel_msg"] = msg
        self.switch_to = sid
        self.quit_requested = True

    def server_dialog(self, sid=None):
        """Add a server (sid None) or edit one: a small window with name / address / password."""
        self.release_all()
        entry = self.cfg.server(sid) if sid else None
        before = (entry or {}).get("url"), (entry or {}).get("key")
        self._pause_ds_audio(True)
        try:
            self._server_dialog(sid, entry, before)
        finally:
            self._pause_ds_audio(False)

    def _server_dialog(self, sid, entry, before):
        from .setup_dialog import server_form

        def check(label, url, key):
            try:
                if entry:
                    self.cfg.edit_server(sid, label, url, key)
                else:
                    check.added = self.cfg.add_server(label, url, key)
            except ValueError as e:
                return str(e)
            return None
        check.added = None
        cfg = self.cfg
        saves = [(None, "Its own save (a separate game)")] + [
            (o["id"], f"Same save as {cfg.server_label(o)}"[:40]) for o in cfg.servers
            if o is not entry and not o.get("save_dir")]
        current = (entry or {}).get("save_dir")
        try:
            res = server_form(entry, check, saves, current)
        except Exception:  # noqa: BLE001
            log.exception("server window failed")
            self.toast("Couldn't open the server window.", 4, ui.BAD)
            return
        if not res:
            return
        choice = res.get("save")
        target = check.added["id"] if check.added is not None else sid
        if choice != current:
            if target == cfg.server_id:
                # the running game writes into the current folder until it closes: switch saves after that
                self.pending_share = (target, choice)
                self._restart_on(target, "Switching saves...")
                return
            try:
                cfg.share_save(target, choice)
            except ValueError as e:
                self.toast(str(e), 5, ui.BAD)
                return
        if check.added is not None:
            name = self.cfg.server_label(check.added)
            if res.get("join"):
                self.switch_server(check.added["id"])
            else:
                self.toast(f"Added {name}. Click Switch to play there.", 5, ui.GOOD)
        elif sid == self.cfg.server_id and (entry.get("url"), entry.get("key")) != before:
            if self.chal or self.trade or self.battle or self.live:
                self.toast("Saved. The new address/password is used the next time you start the game.", 5, ui.GOOD)
            else:
                self._restart_on(sid, "Reconnecting...")     # save, close and reopen with the new details
        else:
            self.toast("Saved.", 3, ui.GOOD)

    def remove_server(self, sid):
        """Two clicks: the first arms it (the button turns into 'Sure?')."""
        if not self.confirming_remove(sid):
            self._confirm_remove = (sid, time.time())
            return
        self._confirm_remove = None
        entry = self.cfg.server(sid)
        try:
            self.cfg.remove_server(sid)
        except ValueError as e:
            self.toast(str(e), 4, ui.BAD)
            return
        self.toast(f"Removed {self.cfg.server_label(entry or {})}. Its saves stay in your YellowLink folder.", 5)

    def confirming_remove(self, sid) -> bool:
        c = self._confirm_remove
        return bool(c and c[0] == sid and time.time() - c[1] < 5)

    def pick_region_game(self, region_id, game_id):
        """Remember which version to use for a region; if we're in that region, switch to it."""
        picks = self.cfg.setdefault("region_game", {})
        picks[region_id] = game_id
        self.cfg.save()
        if self.game_info.region == region_id and game_id != self.game_id:
            self.travel(game_id)

    def add_rom_dialog(self):
        self.release_all()
        self._pause_ds_audio(True)
        try:
            self._add_rom_dialog()
        finally:
            self._pause_ds_audio(False)

    def _add_rom_dialog(self):
        try:
            import tkinter as tk
            from tkinter import filedialog
            root = tk.Tk()
            root.withdraw()
            path = filedialog.askopenfilename(title="Add a Pokémon game file",
                                              filetypes=library.GAME_FILE_TYPES)
            root.destroy()
        except Exception:  # noqa: BLE001
            log.exception("file dialog failed")
            self.toast("Couldn't open the file picker.", color=ui.BAD)
            return
        if not path:
            return
        self._loading_screen("Adding your game file...")
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
        self._sync_ds_audio()

    def _sync_ds_audio(self):
        """DeSmuME plays Platinum's sound itself: keep its volume in step with our slider / mute / fast-forward."""
        if self.nds is not None and not self.nds.closed:
            self.nds.set_volume(self._ds_volume())

    def _pause_ds_audio(self, paused):
        """Around anything that stops the main loop (file pickers, report window): no looping sound."""
        if not paused and getattr(self, "_ds_chal_paused", False):
            return                       # the Soul Link is still waiting for the partner: stay silent
        if self.nds is not None and not self.nds.closed:
            try:
                self.nds.pause_audio(paused)
            except Exception:  # noqa: BLE001
                log.exception("DS audio pause failed")

    def _set_volume_from_x(self, x):
        bar = self.volume_bar
        if bar and bar.w:
            self.set_volume(round((x - bar.x) / bar.w * 100))

    def release_all(self):
        for b in list(self.held):
            self.emu.button_release(b)
        self.held.clear()
        self.kb_held.clear()
        self.pad_held.clear()
        if self.touching or self._touch_release:
            self.nds.touch_release()
            self.touching = self._touch_release = False

    # DS touch screen (mouse on the bottom screen) -----------------------------------------------------------
    def _touch_point(self, pos, clamp=False):
        """Window position -> bottom-screen pixel (or None if outside it)."""
        if not self.ds or self.scale <= 0:
            return None
        gx = pos[0] / self.scale
        gy = (pos[1] - self.game_y) / self.scale - nds_core.SCREEN_H
        if clamp:
            return (int(max(0, min(nds_core.SCREEN_W - 1, gx))), int(max(0, min(nds_core.SCREEN_H - 1, gy))))
        if 0 <= gx < nds_core.SCREEN_W and 0 <= gy < nds_core.SCREEN_H:
            return int(gx), int(gy)
        return None

    def touch_down(self, pos) -> bool:
        if self.mode != "play" or self.nds is None:
            return False
        pt = self._touch_point(pos)
        if pt is None:
            return False
        self.nds.touch(*pt)
        self.touching, self._touch_release, self._touch_frames = True, False, 0
        return True

    def touch_move(self, pos):
        if self.touching:
            self.nds.touch(*self._touch_point(pos, clamp=True))

    def touch_up(self):
        if self.touching:
            self.touching = False
            self._touch_release = True   # released after the game has seen the touch for a couple of frames

    def _touch_step(self, ticks):
        if self.touching or self._touch_release:
            self._touch_frames += ticks
        if self._touch_release and self._touch_frames >= 3:
            self.nds.touch_release()
            self._touch_release = False

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
            self.emu.button_press(b)
            self.held.add(b)
        elif not want and b in self.held:
            self.emu.button_release(b)
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
                if ev.key in self.keymap:
                    self.press(self.keymap[ev.key], False)
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
                    elif not hit and self.ds:
                        self.touch_down(ev.pos)     # DS touch screen
            elif ev.type == pygame.MOUSEWHEEL and self.mode == "trade":
                self.trade_scroll = max(0, self.trade_scroll - ev.y)
            elif ev.type == pygame.MOUSEWHEEL:
                if self.chat_rect and self.chat_rect.collidepoint(pygame.mouse.get_pos()):
                    self.chat_scroll = max(0, self.chat_scroll + ev.y * 3)
            elif ev.type == pygame.MOUSEMOTION and self.dragging_volume:
                self._set_volume_from_x(ev.pos[0])
            elif ev.type == pygame.MOUSEMOTION and self.touching:
                self.touch_move(ev.pos)
            elif ev.type == pygame.MOUSEBUTTONUP and ev.button == 1 and self.touching:
                self.touch_up()
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
            elif ev.type == pygame.JOYBUTTONDOWN and ev.button in self.pad_buttons:
                log.info("pad button %s", ev.button)
                self.press(self.pad_buttons[ev.button], True, "pad")
            elif ev.type == pygame.JOYBUTTONUP and ev.button in self.pad_buttons:
                self.press(self.pad_buttons[ev.button], False, "pad")
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

    def _answer_prompt(self, accept: bool):
        """Accept/decline the open invite (trade, gift, battle or challenge) - keys or buttons."""
        p = self.prompt
        if not p or time.time() - (p.get("shown") or 0) < 0.5:
            return
        if p["kind"] == "challenge":
            self.respond_challenge(accept)
        elif p["kind"] == "tradeevo":
            self.respond_tradeevo(accept)
        elif p["kind"] == "battle":
            self.net.send({"type": "battle_respond", "battle_id": p["id"], "accept": accept})
        else:
            self.net.send({"type": "trade_respond", "trade_id": p["id"], "accept": accept})
        self.prompt = None
        if self.mode == "prompt":
            self.mode = "play"
        self.release_all()

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
                    self.action_trade(e)
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
        if self.mode == "evo_wait":
            if k == pygame.K_ESCAPE:
                self.cancel_tradeevo()
            return
        if self.mode == "prompt":
            p = self.prompt
            if not p or time.time() - (p.get("shown") or 0) < 0.5:
                return
            if k in (pygame.K_y, pygame.K_RETURN, pygame.K_n, pygame.K_ESCAPE):
                self._answer_prompt(k in (pygame.K_y, pygame.K_RETURN))
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
            self.trade_key(k)
            return

        # play mode
        if k in self.keymap:
            self.press(self.keymap[k], True)
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
        elif k == pygame.K_SPACE:                  # toggle (press once = fast, again = normal)
            self.fast = not self.fast
            self._sync_ds_audio()
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
            self._sync_ds_audio()
            self.toast("Sound off" if self.audio.muted else "Sound on", 1.5)
        elif k == pygame.K_n:
            self.cfg["show_names"] = not self.cfg.get("show_names", True)
            self.cfg.save()
        elif k in (pygame.K_F5, pygame.K_F6):
            step = (0.5 if self.ds else 1) * (1 if k == pygame.K_F6 else -1)
            self.cfg[self._scale_key()] = max(1, min(6, self.scale + step))
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
        if self.ds:
            self._update_friends_ds()
            return
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

    # Platinum: friends walking around in Sinnoh (sinnoh_view.py) ---------------------------------------------
    def _sinnoh(self):
        """The Platinum friend renderer (camera history + Lucas/Dawn decoded from the player's ROM) or None."""
        sv = getattr(self, "_sv", None)
        if sv is None and self.full and self.ds:
            sv = self._sv = sinnoh_view.SinnohView(self.cfg["rom_path"])
            if sv.error:
                log.warning("friends in Sinnoh disabled: %s", sv.error)
        return sv if sv is not None and not sv.error else None

    def _update_friends_ds(self):
        """Platinum: remember the camera (the top screen shows it a few frames late) and glide friends."""
        sv = self._sinnoh()
        if sv is None:
            return
        g = self.game
        fs = g.field_system() if g.overworld_running() else None
        me = None
        if fs:
            p = g.position()
            me = sinnoh_view.billboard_anchor(p["px"], p["ph"], p["py"])
        sv.record_camera(self.nds, fs, self.nds.frame_count, me)
        for f in self.friends.values():
            st = f.state
            if not st:
                continue
            if f.cur_map != st.get("map"):
                self._snap(f)
            idle = sv.idle.get(f.uid, 99)
            f.cur_px, f.cur_py, moved = sinnoh_view.glide(f.cur_px, f.cur_py, st.get("px", 0), st.get("py", 0))
            if moved:
                f.anim = (f.anim if idle < 6 else 8) + moved      # first half tile shows a step, like the game
                sv.idle[f.uid] = 0
            else:
                sv.idle[f.uid] = idle + 1

    def _draw_friends_ds(self):
        """Platinum: draw friends on the top screen like NPCs; returns name tags [(name, x, y)] in view pixels."""
        sv = self._sinnoh()
        g = self.game
        if sv is None or not g.overworld_running() or g.field_task():
            return []          # battle, menus/other screens (bag, PC...), text boxes, scripts, warps
        cam, me = sv.display(self.nds.frame_count)
        if cam is None:
            return []
        p = g.position()
        if self.frame % 30 == 0 and me and g.is_safe() and p.get("state") == 0:
            sv.update_tint(self.nds.screen_rgb(), cam, me, p.get("gender", 0), p.get("facing", 1))
        matrix_of = sv.matrix_of(self.nds)
        my_map = p.get("map", 0)
        show = []
        for f in self.friends.values():
            st = f.state
            if not st or not st.get("visible") or st.get("game") != self.game_id \
                    or st.get("challenge") != (self.chal or {}).get("id") \
                    or not sinnoh_view.same_space(my_map, st.get("map", -1), matrix_of):
                continue
            info = sinnoh_view.unpack_img(st.get("img", 0))
            kind = info["kind"] if info["kind"] in (0, 1) else 0
            step = sinnoh_view.walk_step(f.anim) if sv.idle.get(f.uid, 99) < 6 else 0
            frame = sinnoh_view.frame_index(st.get("facing", 1), step, info["running"] and kind == 0)
            show.append(sinnoh_view.FriendSprite(f.name, sinnoh_view.billboard_anchor(f.cur_px, info["ph"], f.cur_py),
                                                 info["gender"], frame, kind))
        if not show:
            return []
        my_kind = 1 if p.get("state") == 1 else 0
        tex = sv.sprites.get((p.get("gender", 0) & 1, my_kind))
        mask = [tex["frames"][sinnoh_view.frame_index(p.get("facing", 1), k, p.get("running") and not my_kind)]
                for k in range(4)] if tex and me else []
        return sv.draw(self.game_surf, cam, show, me, mask)

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
        if self.ds:
            # both DS screens (top above the touch screen); friends in Sinnoh are drawn on the top screen
            self.game_surf.blit(pygame.image.frombuffer(self.nds.screen_rgb(), (self.view_w, self.view_h), "RGB"),
                                (0, 0))
            tags = self._draw_friends_ds()
        else:
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
        if self.wild_shiny and not self.ds:
            # Yellow wild battle: this one would be shiny in Gen 2 (counts as shiny in YellowLink) - next to the enemy
            badge_art.draw_shiny_indicator(sub, self.fonts, int((self.off_x + SHINY_TAG_X) * self.scale),
                                           int((self.off_y + SHINY_TAG_Y) * self.scale), self.scale)
        if self.wild_owned and not self.ds and self.game_id == "yellow" and self.game.enemy_hud_shown():
            badge_art.draw_owned_ball(sub, int((self.off_x + OWNED_BALL_X) * self.scale),
                                      int((self.off_y + OWNED_BALL_Y) * self.scale), self.scale)
        if self.mode == "panel" and self.panel == "multiplayer":
            panels.draw_multiplayer(sub, self.fonts, self, self.game_w, self.game_h, (0, self.game_y))
        elif self.mode == "panel" and self.panel == "settings":
            panels.draw_settings(sub, self.fonts, self, self.game_w, self.game_h, (0, self.game_y))
        elif self.mode == "panel" and self.panel == "servers":
            panels.draw_servers(sub, self.fonts, self, self.game_w, self.game_h, (0, self.game_y))
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
            panels.draw_trade(sub, self.fonts, self, self.game_w, self.game_h, (0, self.game_y))
        elif self.mode == "prompt" and self.prompt:
            left = max(0, int(self.prompt["until"] - time.time()))
            if self.prompt["kind"] == "challenge":
                p = self.prompt
                game = library.GAMES_BY_ID.get(p["game"])
                rules = p.get("rules") or {}
                if p.get("continue"):
                    lines = [f"{p['name']} wants to continue your Soul Link!",
                             (f"{game.title if game else p['game']} - you'll both jump into your run.", ui.DIM),
                             (panels.rules_line(rules, p["game"]).capitalize(), ui.DIM)]
                else:
                    lines = [f"{p['name']} challenges you to a Soul Link!",
                             (f"{game.title if game else p['game']}, fresh save (your main game is kept safe).",
                              ui.DIM),
                             (panels.rules_line(rules, p["game"]).capitalize(), ui.DIM)]
                r = ui.modal(sub, self.fonts, self.game_w, self.game_h, "Challenge!", lines + [(f"({left}s)", ui.DIM), ""],
                             "[Y] accept    [N] decline")
                off = (0, self.game_y)
                panels.button(sub, self, self.fonts, pygame.Rect(r.right - 250, r.bottom - 52, 110, 36), "Accept",
                              lambda: self.respond_challenge(True), offset=off, primary=True)
                panels.button(sub, self, self.fonts, pygame.Rect(r.right - 128, r.bottom - 52, 110, 36), "Decline",
                              lambda: self.respond_challenge(False), offset=off)
            elif self.prompt["kind"] == "tradeevo":
                p = self.prompt
                name = self.dex_name(p.get("dex", 0))
                nick = p.get("nick") or ""
                mon = name if not nick or nick.upper() == name.upper() else f"{nick} ({name})"
                width = min(self.game_w - 40, 520) - 36
                lines = list(ui.wrap(self.fonts.body, f"Help {p['name']} evolve {mon}?", width))
                dim = f"Trade and trade back: it becomes {self.dex_name(p.get('into', 0))}." + (
                    f" Its {p['item']} is used up." if p.get("item") else "") + " Nothing changes in your game."
                lines += [(ln, ui.DIM) for ln in ui.wrap(self.fonts.body, dim, width)]
                lines += [(f"({left}s)", ui.DIM), ""]
                r = ui.modal(sub, self.fonts, self.game_w, self.game_h, "Trade evolution", lines,
                             "[Y] yes    [N] no")
                off = (0, self.game_y)
                panels.button(sub, self, self.fonts, pygame.Rect(r.right - 250, r.bottom - 52, 110, 36), "Yes",
                              lambda: self._answer_prompt(True), offset=off, primary=True)
                panels.button(sub, self, self.fonts, pygame.Rect(r.right - 128, r.bottom - 52, 110, 36), "No",
                              lambda: self._answer_prompt(False), offset=off)
            elif self.prompt["kind"] == "battle":
                ui.modal(sub, self.fonts, self.game_w, self.game_h, "Battle challenge!",
                         [f"{self.prompt['name']} challenges you to a live battle!",
                          ("Exhibition match: teams fully healed, nothing is lost.", ui.DIM),
                          (f"({left}s)", ui.DIM)],
                         "[Y] accept    [N] decline")
            else:
                gift = self.prompt["kind"] == "gift"
                lines = ([f"{self.prompt['name']} wants to give you a Pokémon!",
                          ("It goes straight into your PC.", ui.DIM)] if gift else
                         [f"{self.prompt['name']} wants to trade with you!",
                          ("You both pick a Pokémon from your PC (works across games).", ui.DIM)])
                r = ui.modal(sub, self.fonts, self.game_w, self.game_h, "Gift!" if gift else "Trade request",
                             lines + [(f"({left}s)", ui.DIM), ""], "[Y] accept    [N] decline")
                off = (0, self.game_y)
                panels.button(sub, self, self.fonts, pygame.Rect(r.right - 250, r.bottom - 52, 110, 36), "Accept",
                              lambda: self._answer_prompt(True), offset=off, primary=True)
                panels.button(sub, self, self.fonts, pygame.Rect(r.right - 128, r.bottom - 52, 110, 36), "Decline",
                              lambda: self._answer_prompt(False), offset=off)
        elif self.mode == "evo_wait" and self.evo_out:
            o = self.evo_out
            _, partner = self.chal_partner()
            asking = o["state"] == "asking"
            r = ui.modal(sub, self.fonts, self.game_w, self.game_h, "Trade evolution",
                         [f"{o['opt'].label()} -> {o['opt'].into_name}",
                          (f"Waiting for {partner} to say yes..." if asking else "Evolving...", ui.DIM), ""],
                         "[Esc] cancel" if asking else "")
            if asking:
                panels.button(sub, self, self.fonts, pygame.Rect(r.right - 128, r.bottom - 52, 110, 36), "Cancel",
                              self.cancel_tradeevo, offset=(0, self.game_y))
        elif self.mode == "battle_confirm" and self.battle_offer:
            o = self.battle_offer
            lines = [f"{self.rom.species_name(m.species)}  Lv {m.level}" for m in o["mons"]]
            lines.append(("Exhibition match: both teams fully healed,", ui.DIM))
            lines.append(("nothing is won or lost.", ui.DIM))
            ui.modal(sub, self.fonts, self.game_w, self.game_h, f"Battle {o['name']}'s team?", lines,
                     "[Enter] fight!    [Esc] back")
        elif self.mode == "help":
            ui.draw_help(sub, self.fonts, self.game_w, self.game_h, ds=self.ds)
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
        banner = self.banners.current()
        if banner is not None:            # shiny alert: over everything, never takes input
            badge_art.draw_shiny_banner(sub, self.fonts, banner)
        ui.draw_toasts(sub, self.fonts, self.toasts, self.game_w,
                       bottom=banner is not None or
                       self.mode in ("panel", "chal_popup", "prompt", "battle_confirm", "evo_wait"))
        if self.fast:
            ui.text(sub, self.fonts.body, ">> fast (Space)", (self.game_w - 10, 10), ui.ACCENT, anchor="topright")
        ui.draw_sidebar(self.screen, self.fonts, self, self.game_w, self.screen.get_height())
        pygame.display.flip()

    # ----------------------------------------------------------------- loop
    def step(self):
        """Advance one host frame (network, emulation, publishing)."""
        self.handle_net()
        paused = self.chal_paused()
        if self.ds:
            if paused != getattr(self, "_ds_chal_paused", False):
                # waiting for the Soul Link partner: no emulation, so stop DeSmuME's sound (it loops otherwise)
                self._ds_chal_paused = paused
                self._pause_ds_audio(paused)
            ticks = 0 if paused else (DS_FAST_TICKS if self.fast and not self.trade else 1)
            if ticks:
                self.nds.tick(ticks)      # DeSmuME plays the sound itself (see _sync_ds_audio)
                self._pt_pc_poll(ticks)
            self._touch_step(ticks)
        else:
            ticks = 0 if paused else (4 if self.fast and not self.trade else 1)
        for i in range(0 if self.ds else ticks):
            self.pyboy.tick(1, i == ticks - 1)
            if ticks == 1:
                self.audio.push(self.pyboy.sound.ndarray)
            self.update_battle()
            self.update_live()
        self.chal_step()
        self.trades_step()
        self.badges_step()
        self.shiny_step()
        if self.update_dl and self.update_dl.status != "downloading" and not getattr(self.update_dl, "announced", False):
            self.update_dl.announced = True
            if self.update_dl.status == "ready":
                if self.mode == "play":
                    self.mode = "update_ready"
            else:
                self.toast("Update download failed - press U to try again.", 6, ui.BAD)
                self.update_dl = None
        self._maybe_show_prompt()
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
        if self.frame % (DS_SAVE_POLL if self.ds else SRAM_FLUSH_EVERY) == 0:
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
                next_t += self.frame_time
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

    def _await_pc_ack(self, timeout=3.0):
        """Give the server a moment to confirm our last PC upload before we close: an unconfirmed upload leaves
        this server's sync state one step behind (harmless - see pcdb 'left' - but it costs a merge next time)."""
        try:
            if self.chal or self.net.status != "online" or self.pcsync.uploading is None:
                return
            end = time.time() + timeout
            while self.pcsync.uploading is not None and self.net.status == "online" and time.time() < end:
                self.handle_net()
                time.sleep(0.03)
        except Exception:  # noqa: BLE001 - closing must never fail over this
            log.exception("waiting for the PC upload confirmation failed")

    # Exp Share All (Settings) ----------------------------------------------------------------------------
    def exp_share_on(self) -> bool:
        """Asked by the game hooks at every experience award: the modern Exp. Share rule applies now?
        Never in live or practice battles with friends (those give no lasting experience anyway). A practice
        offer that's still waiting doesn't count (the player may be in a normal battle meanwhile)."""
        practice = self.battle is not None and getattr(self.battle, "phase", "") != "waiting"
        run = self.chal_exp_share()            # in a Soul Link: the run's rule for both players (SPEC.md 7)
        on = bool(self.cfg.get("exp_share_all", True)) if run is None else run
        return on and not practice and not self.live

    def _exp_award_busy(self) -> bool:
        h = self.exp_share
        if h is None or not hasattr(h, "busy"):
            return False
        try:
            return bool(h.busy())
        except Exception:  # noqa: BLE001 - unsure: don't snapshot (the older snapshot is always consistent)
            log.exception("Exp Share busy() failed")
            return True

    def _settle_exp_award(self, max_frames=None, max_seconds=3.0) -> bool:
        """Closing/leaving while the game hands out experience (busy(): no snapshot possible). Let the game run on,
        no buttons held, until the award is over (DS: up to ~10 s of game time, GB: ~20 s, at most `max_seconds`).
        Still busy (it waits for a button on a text box, a level-up window or a move prompt):
          * in a Soul Link the resume must be NOW - an older one makes the referee think mons caught since then
            are gone (and kill them for both players). The hooks are removed, so the rest of this one award goes
            by the game's own rule (Platinum: our changes are undone first), and the snapshot is taken from there;
          * in the main game the last snapshot (from before this battle) is kept, as before.
        Returns True when a snapshot can be taken now."""
        if not self._exp_award_busy():
            return True
        frames = max_frames if max_frames is not None else (600 if self.ds else 1200)
        try:
            self.release_all()
        except Exception:  # noqa: BLE001
            log.exception("release_all before settling the exp award failed")
        if self.ds:
            self._pause_ds_audio(True)
        start, n = time.monotonic(), 0
        try:
            while n < frames and self._exp_award_busy() and time.monotonic() - start < max_seconds:
                if self.ds:
                    self.nds.tick(1)
                else:
                    self.pyboy.tick(1, False)
                n += 1
        except Exception:  # noqa: BLE001
            log.exception("running the game to the end of the exp award failed")
        if not self._exp_award_busy():
            log.info("exp award finished after %d frame(s); snapshot can be taken", n)
            return True
        if self.chal:
            log.warning("exp award still running after %d frame(s) (waits for a button): Soul Link needs a current "
                        "resume - removing the Exp Share hooks, the rest of this award uses the game's own rule", n)
            self._remove_exp_share()
            return True
        log.info("exp award still running after %d frame(s): keeping the last snapshot", n)
        return False

    def _install_exp_share(self):
        """Every adapter can offer install_exp_share(emulator, enabled) (Platinum: platinum_exp). Only on the
        verified ROMs (it hooks game code)."""
        if not self.full or self.exp_share is not None:
            return
        try:
            if self.ds:
                import importlib
                module = importlib.import_module(".platinum_exp", __package__)
                emu = self.nds
            else:
                module, emu = self.G, self.pyboy
            install = getattr(module, "install_exp_share", None)
            if install is not None:
                self.exp_share = install(emu, self.exp_share_on)
                log.info("Exp Share All hooks installed for %s (setting %s)", self.game_id,
                         "on" if self.exp_share_on() else "off")
        except Exception:  # noqa: BLE001 - the game must run without it
            log.exception("could not install Exp Share All for %s", self.game_id)
            self.exp_share = None

    def set_setting(self, key, value, message=None):
        self.cfg[key] = value
        self.cfg.save()
        if message:
            self.toast(message, 3, ui.GOOD if value else ui.DIM)

    def _remove_exp_share(self):
        if self.exp_share is not None:
            try:
                self.exp_share.remove()
            except Exception:  # noqa: BLE001
                log.exception("removing the Exp Share All hooks failed")
            self.exp_share = None

    def shutdown(self):
        try:
            if self.battle:
                self._restore_battle()
            if self.live:
                self.net.send({"type": "battle_leave", "battle_id": self.live.battle_id})
                self._end_live("", ui.DIM)
            self._settle_exp_award()             # mid-award: let it finish (Soul Link: never keep an old resume)
            self.autosave(force=True)            # skipped while experience is still being handed out
            self.flush_sram(force=True)
            self._remove_exp_share()                # after the snapshot (removing mid-award changes game RAM)
            if self.ds:
                self.nds.close()          # also blanks DeSmuME's shared battery file (see nds_core)
            else:
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
            self._remove_exp_share()
            if self.nds is not None and not self.nds.closed:
                try:
                    self.nds.close(blank_battery=False)
                except Exception:  # noqa: BLE001
                    pass
        self.chal_shutdown()
        try:
            self.audio.close()
        except Exception:  # noqa: BLE001
            pass
        self._await_pc_ack()
        self.net.stop()
        thread = getattr(self.net, "_thread", None)
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.5)          # let the last messages (e.g. "left the challenge") go out
        pygame.quit()


def selftest_ds(argv) -> int:
    """`YellowLink --selftest-ds <report.txt> [test.nds]`: start pygame, our sound device and DeSmuME the way the
    game does (used by the release build to prove the exe can run Platinum; a missing DLL fails the build)."""
    i = argv.index("--selftest-ds")
    out = argv[i + 1] if len(argv) > i + 1 else "selftest-ds.txt"
    rom = argv[i + 2] if len(argv) > i + 2 else None
    lines = []
    code = 0
    try:
        pygame.init()
        pygame.display.set_mode((64, 64))
        audio = AudioOut(50)
        lines.append(f"pygame {pygame.version.ver} SDL {'.'.join(map(str, pygame.get_sdl_version()))}, "
                     f"our audio {'mixer' if audio.ok else 'off'}")
        if rom == "make":
            rom = nds_core.make_test_rom(os.path.join(os.path.dirname(os.path.abspath(out)), "yltest.nds"))
        lines.append(nds_core.selftest(rom))
        audio.close()
        lines.append("OK")
    except Exception:  # noqa: BLE001
        import traceback
        lines.append(traceback.format_exc())
        code = 1
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return code


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if "--selftest-ds" in argv:
        sys.exit(selftest_ds(argv))
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


def _ds_fallback(cfg, diag, err) -> bool:
    """Platinum can't run on this PC: play a Game Boy game instead (the reason is shown in the game). With no
    Game Boy game added, explain it in a window and open the settings to pick another game file.
    Returns False when the player closes that without choosing one (the app then exits - no crash loop)."""
    lib = library.Library(cfg, app_dir())
    other = next((g for g in lib.games if library.GAMES_BY_ID[g].platform != "nds"), None)
    if other is not None:
        lib.set_current(other)
        cfg["_ds_error"] = str(err)
        return True
    text = diag.report_text(f"Pokémon Platinum could not start: {err}") if diag else str(err)
    show_report_window(
        text, "YellowLink – Pokémon Platinum couldn't start",
        f"Pokémon Platinum couldn't start on this PC ({err}). Please send this to the host. Meanwhile you can "
        "play Pokémon Yellow or Crystal: choose that game file in the next window.",
        (lambda: send_report_sync(cfg, text, "DS start failed")) if cfg.is_complete() else None,
        diag.dir if diag else None)
    from .setup_dialog import run_setup
    chosen = run_setup(cfg, first_run=False)
    current = library.Library(cfg, app_dir()).current
    return bool(chosen and current and library.GAMES_BY_ID[current].platform != "nds")


def _main(argv, cfg, diag):
    # Always start in the main game (crashed or closed inside a challenge), and before the settings window: an
    # imported save must never land in a challenge's save folder.
    if cfg.pop("active_challenge", None):
        cfg.save()
    if "--setup" in argv or not cfg.is_complete():
        from .setup_dialog import run_setup
        if not run_setup(cfg, first_run=not cfg.is_complete()):
            return
    while True:
        errors_before = diag.errors
        try:
            app = App(cfg, diag)
        except nds_core.DSUnavailable as e:
            # never get stuck crashing on start: go back to a Game Boy game and say why
            log.exception("DS game could not start")
            if cfg.pop("active_challenge", None) is not None:     # a Platinum Soul Link: back to the main game
                cfg.save()                                         # (the partner gets "didn't load in")
            if not _ds_fallback(cfg, diag, e):
                return
            continue
        app.run()
        if getattr(app, "pending_share", None):           # the save choice of the server we were on
            try:
                cfg.share_save(*app.pending_share)
            except ValueError:
                log.exception("could not change which save the server uses")
        if app.switch_to:                                 # another server: its own saves, PC and challenges
            cfg.switch_server(app.switch_to)
            lib = library.Library(cfg, app_dir())
            if lib.current:
                lib.set_current(lib.current)
            continue
        if app.enter_challenge:
            cfg["active_challenge"] = app.enter_challenge
            cfg.save()
            continue
        if cfg.pop("active_challenge", None) is not None:   # any other way out of a challenge: main game next
            cfg.save()
        if app.leave_challenge:
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
