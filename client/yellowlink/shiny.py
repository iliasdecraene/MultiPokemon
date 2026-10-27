"""Shiny alerts (SPEC.md section 10): a shiny in your wild battle -> everyone gets a chat line, a banner and a jingle.

Game side: the adapters' verified `battle_info()` (game / crystal / platinum), read every POLL_EVERY frames and
settled once per battle by `EncounterWatch`. Network: `shiny_found` to servers listing `shiny_alert`; the server
answers everyone with a `system` line carrying a `shiny` field. Here also the banner queue, the jingle (synthesised
with numpy - no game audio) and, for Yellow, the "already caught" flag behind the Poké Ball next to the enemy's name.
"""
from __future__ import annotations

import logging
import math
import re
import time
from dataclasses import dataclass

import numpy as np
import pygame

log = logging.getLogger("yellowlink.shiny")

POLL_EVERY = 15             # frames between battle_info() reads
STABLE_READS = 2            # identical reads in a row that settle a battle
END_READS = 3               # reads without a battle that end it
BANNER_SECS = 5.0
MAX_QUEUED = 3              # banners waiting behind the one showing
FEATURE = "shiny_alert"     # welcome.features
SPECIES_MAX, AREA_MAX = 12, 40
JINGLE_VOLUME = 0.5         # peak of the jingle (fraction of full scale) - fixed, the volume slider doesn't apply
JINGLE_RATE = 48000
_SPECIES_OK = re.compile(r"^[^\W_][\w .'’♀♂:-]{0,11}$")     # what the server accepts (SPEC.md section 10)


# --- detection ------------------------------------------------------------------------------------------------
def gen4_key_shiny(key) -> bool:
    """Platinum mon key (PID 8 hex + TID 4 + SID 4, platinum.battle_info()["keys"]): shiny by the Gen-4 rule."""
    if not isinstance(key, str) or len(key) != 16:
        return False
    try:
        pid, tid, sid = int(key[:8], 16), int(key[8:12], 16), int(key[12:], 16)
    except ValueError:
        return False
    return (tid ^ sid ^ (pid >> 16) ^ (pid & 0xFFFF)) < 8


def wild_shinies(info) -> list:
    """Dex numbers of the shiny wild mons in a battle_info() reading ([] for trainer/special battles). Platinum lists
    every wild mon (`keys`/`dexes`, a double battle has two); Yellow/Crystal have one (`dex`, `shiny`)."""
    if not info or info.get("kind") != "wild":
        return []
    keys, dexes = info.get("keys"), info.get("dexes")
    if keys and dexes and len(keys) == len(dexes):
        return [d for k, d in zip(keys, dexes) if d and gen4_key_shiny(k)]
    dex = info.get("dex") or 0
    return [dex] if info.get("shiny") and dex else []


def _sig(info) -> tuple:
    return (info.get("kind"), info.get("dex"), bool(info.get("shiny")), tuple(info.get("keys") or ()),
            info.get("dvs"))


class EncounterWatch:
    """Feed one battle_info() reading per poll; `feed` returns the reading once per battle, when it settled
    (STABLE_READS identical readings in a row). The battle ends after END_READS readings without one, so a single
    unreadable frame (Platinum) can't make the same battle 'new' again."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.in_battle = False
        self.done = False
        self._last = None
        self._count = 0
        self._none = 0

    def feed(self, info):
        if info is None:
            if self.in_battle:
                self._none += 1
                if self._none >= END_READS:
                    self.reset()
            return None
        self._none = 0
        self.in_battle = True
        if self.done:
            return None
        sig = _sig(info)
        if sig == self._last:
            self._count += 1
        else:
            self._last, self._count = sig, 1
        if self._count >= STABLE_READS:
            self.done = True
            return info
        return None


# --- message --------------------------------------------------------------------------------------------------
def plain_gender(name: str) -> str:
    """Nidoran♀ -> Nidoran F, Nidoran♂ -> Nidoran M (our font has no ♀/♂ glyphs: no box in banners or chat)."""
    return name.replace("♀", " F").replace("♂", " M").replace("  ", " ")


def species_label(name: str) -> str:
    """Display name from the game's own (upper-case Gen 1/2) name: NIDORAN♀ -> Nidoran F, MR.MIME -> Mr.Mime."""
    return plain_gender(name.title() if name[:1] != "#" else name)


def area_phrase(area: str) -> str:
    """'on Route 1' / 'on Route 202', otherwise 'in Viridian Forest', 'in Mt. Moon', 'in Eterna City'."""
    return f"on {area}" if area.startswith("Route") else f"in {area}"


def found_message(species: str, dex: int, area: str, game: str) -> dict:
    return {"type": "shiny_found", "species": str(species).strip()[:SPECIES_MAX], "dex": int(dex),
            "area": str(area).strip()[:AREA_MAX], "game": game}


# --- banners --------------------------------------------------------------------------------------------------
@dataclass
class Banner:
    title: str
    sub: str
    start: float = 0.0

    def age(self, now: float) -> float:
        return now - self.start

    def alpha(self, now: float) -> float:
        """0..1: in over 0.35 s, out over the last 0.6 s."""
        a = self.age(now)
        return max(0.0, min(1.0, a / 0.35, (BANNER_SECS - a) / 0.6))


class Banners:
    def __init__(self):
        self.queue: list = []
        self.cur: Banner | None = None

    def push(self, title: str, sub: str = "", now: float | None = None):
        if len(self.queue) >= MAX_QUEUED:
            self.queue.pop(0)                       # a flood: the oldest waiting one goes
        self.queue.append(Banner(title, sub))

    def update(self, now: float | None = None):
        """Advance; returns the banner that starts showing now (for the jingle), else None."""
        now = time.time() if now is None else now
        if self.cur is not None and self.cur.age(now) >= BANNER_SECS:
            self.cur = None
        if self.cur is None and self.queue:
            self.cur = self.queue.pop(0)
            self.cur.start = now
            return self.cur
        return None

    def current(self, now: float | None = None):
        now = time.time() if now is None else now
        if self.cur is not None and self.cur.age(now) >= BANNER_SECS:
            return None
        return self.cur

    def current_title(self):
        c = self.current()
        return c.title if c else None


# --- jingle ---------------------------------------------------------------------------------------------------
# (start s, frequency Hz, decay s, gain): a quick rising E-major arpeggio, a little twinkle, a ringing chord
_NOTES = [(0.00, 1318.5, 0.22, 0.8), (0.07, 1661.2, 0.22, 0.8), (0.14, 1975.5, 0.22, 0.85),
          (0.21, 2637.0, 0.30, 0.9), (0.36, 1975.5, 0.20, 0.55), (0.43, 2637.0, 0.22, 0.65),
          (0.50, 3322.4, 0.30, 0.6),
          (0.62, 1318.5, 0.55, 0.55), (0.62, 1661.2, 0.55, 0.45), (0.62, 1975.5, 0.55, 0.45),
          (0.62, 2637.0, 0.60, 0.5)]
_GLINTS = [(0.30, 5274.0), (0.47, 4434.9), (0.58, 5919.9), (0.75, 4698.6), (0.88, 5274.0), (1.02, 6271.9)]
JINGLE_SECS = 1.45


def jingle_samples(rate: int = JINGLE_RATE, channels: int = 2) -> np.ndarray:
    """The shiny jingle as int16 (n, channels): bell-like tones (fundamental + octave + a slightly inharmonic
    partial, exponential decay) and soft high glints, peak = JINGLE_VOLUME of full scale, silent at both ends."""
    n = int(JINGLE_SECS * rate)
    t = np.arange(n) / rate
    out = np.zeros(n)
    for start, f, decay, gain in _NOTES:
        tt = t - start
        on = tt >= 0
        x = tt[on]
        env = (1 - np.exp(-x / 0.004)) * np.exp(-x / decay)
        tone = (np.sin(2 * math.pi * f * x) + 0.35 * np.sin(2 * math.pi * 2 * f * x) * np.exp(-x / (decay * 0.4))
                + 0.12 * np.sin(2 * math.pi * 2.76 * f * x) * np.exp(-x / (decay * 0.25)))
        out[on] += gain * env * tone
    for start, f in _GLINTS:
        tt = t - start
        on = (tt >= 0) & (tt < 0.25)
        x = tt[on]
        out[on] += 0.18 * (1 - np.exp(-x / 0.002)) * np.exp(-x / 0.05) * np.sin(2 * math.pi * f * x)
    fade = int(0.12 * rate)
    out[-fade:] *= np.linspace(1, 0, fade) ** 2
    out *= JINGLE_VOLUME * 32767 / max(1e-9, float(np.abs(out).max()))
    mono = np.clip(np.round(out), -32767, 32767).astype(np.int16)
    return np.repeat(mono[:, None], channels, axis=1)


_SOUND_CACHE: dict = {}


def jingle_sound():
    """A pygame Sound of the jingle for the mixer as it is opened (rate, format, channels), or None (no mixer)."""
    return make_sound(jingle_samples, _SOUND_CACHE)


def make_sound(samples_fn, cache: dict):
    """A pygame Sound from `samples_fn(rate, channels)` (int16 (n, channels)) for the mixer as it is opened, cached
    per mixer setup in `cache` - or None (no mixer / no sound card). Also used for the raid horn (raid.py)."""
    try:
        init = pygame.mixer.get_init()
        if not init:
            return None
        if init in cache:
            return cache[init]
        rate, fmt, channels = init
        a = samples_fn(rate, channels)
        if fmt in (-16, 16):
            data = a if fmt == -16 else (a.astype(np.int32) + 32768).astype(np.uint16)
        elif fmt in (-8, 8):
            data = (a >> 8).astype(np.int8) if fmt == -8 else ((a >> 8) + 128).astype(np.uint8)
        elif abs(fmt) == 32:
            data = (a.astype(np.float32) / 32768.0)
        else:
            log.warning("jingle: mixer format %s not supported", fmt)
            return None
        snd = pygame.mixer.Sound(buffer=np.ascontiguousarray(data).tobytes())
        cache.clear()
        cache[init] = snd
        return snd
    except Exception as e:  # noqa: BLE001 - no sound is fine, a crash is not
        log.warning("sound unavailable: %s", e)
        return None


# --- the app side ---------------------------------------------------------------------------------------------
class ShinyMixin:
    """Mixed into App: detection in the player's battles, `shiny_found`, banners + jingle, Yellow's caught flag."""

    def init_shiny(self):
        self.shiny_watch = EncounterWatch()
        self.banners = Banners()
        self.wild_owned = False          # Yellow: the wild species was already caught when this battle started

    def shiny_can_watch(self) -> bool:
        # not in a raid battle: the raid notice already said the boss is shiny (SPEC.md section 12)
        return bool(self.full and getattr(self, "game", None) is not None and not self.live and not self.battle
                    and not getattr(self, "raid_battle", None))

    def shiny_step(self):
        """Called every frame from App.step()."""
        if self.frame % POLL_EVERY == 0:
            info = None
            if self.shiny_can_watch():
                try:
                    info = self.game.battle_info()
                except Exception:  # noqa: BLE001 - a bad read must never stop the game
                    log.exception("shiny check: battle_info failed")
                    info = None
            settled = self.shiny_watch.feed(info)
            if not self.shiny_watch.in_battle:
                self.wild_owned = False
            if settled is not None:
                self._shiny_settled(settled)
        if self.banners.update() is not None:
            self.play_shiny_jingle()

    def _shiny_settled(self, info):
        if self.game_id == "yellow" and info.get("kind") == "wild":
            try:
                self.wild_owned = bool(self.game.dex_owned(int(info.get("dex") or 0)))
            except Exception:  # noqa: BLE001
                log.exception("shiny check: dex owned read failed")
        found = wild_shinies(info)
        if found:
            self.shiny_found(found[0])

    def shiny_area(self) -> str:
        try:
            return self.G.area_name(self.G.area_of(self.game.map_id()))
        except Exception:  # noqa: BLE001
            log.exception("shiny: area name failed")
            return "somewhere"

    def shiny_found(self, dex: int):
        species = species_label(self.dex_name(dex))
        area = self.shiny_area()
        log.info("SHINY: %s (#%d) %s in %s%s", species, dex, area_phrase(area), self.game_id,
                 " (Soul Link)" if self.chal else "")
        self.banners.push(f"A shiny {species} appeared!", area_phrase(area))
        net = getattr(self, "net", None)
        sent = False
        if FEATURE in (getattr(self, "server_features", None) or ()) and net is not None and net.status == "online":
            msg = found_message(species, dex, area, self.game_id)
            if not _SPECIES_OK.fullmatch(msg["species"]):
                msg["species"] = f"No {dex}"        # a name the server wouldn't take (unreadable ROM data)
            sent = bool(net.send(msg))
        if not sent:            # offline / older server: keep a line for yourself
            self.sys_msg(f"✨ You found a shiny {species} {area_phrase(area)}!")

    def shiny_on_system(self, msg):
        """A `system` message; if it carries a `shiny` alert of someone else: banner + jingle (the line itself is
        shown by on_system)."""
        s = msg.get("shiny")
        if not isinstance(s, dict):
            return
        uid, name, species = s.get("uid"), s.get("name"), s.get("species")
        if not all(isinstance(v, str) and v for v in (uid, name, species)):
            return
        if uid == self.cfg["uid"]:
            return                       # our own: the banner already showed when we found it
        area = s.get("area") if isinstance(s.get("area"), str) else ""
        parts = ([area_phrase(area[:AREA_MAX])] if area else []) + (["Soul Link"] if s.get("soullink") is True else [])
        self.banners.push(f"{name[:12]} found a shiny {plain_gender(species[:SPECIES_MAX])}!", " - ".join(parts))

    def play_shiny_jingle(self):
        out = getattr(self, "audio", None)
        if out is not None:
            out.play_fx(jingle_sound())
