"""Soul Link trade evolutions: a "trade and trade back" with the run partner (SPEC.md section 7, 0.7.6).

The player picks a party Pokémon that evolves by trade, the partner says yes, and the mon evolves in its owner's game
through the same evolution code normal YellowLink trades use (game.evolve_for_trade / crystal.evolve_for_trade /
the Platinum rules of server pcstore + the player's ROM). It stays the owner's own mon: OT, IDs, DVs/PID unchanged,
so its Soul Link key, link, deaths and box tracking don't change.

Pure helpers (options / evolve, testable with fake memories) + TradeEvoMixin (the flow, mixed into App).
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass

from . import crystal, game, ui

log = logging.getLogger("yellowlink.tradeevo")

CAP = "trade_evo"            # server feature (welcome.features) + client capability (library.challenge_rules)
APPLY_WITHIN = 30.0          # seconds after "go" to find a safe moment for the write
ASK_WITHIN = 75.0            # no go/end this long after asking (server: 60 s): give up waiting
CRYSTAL_ITEMS = {crystal.KINGS_ROCK: "King's Rock", crystal.METAL_COAT: "Metal Coat",
                 crystal.DRAGON_SCALE: "Dragon Scale", crystal.UP_GRADE: "Up-Grade",
                 crystal.EVERSTONE: "Everstone"}
G4_EVERSTONE = 229           # Platinum item id (pret generated/items.txt; server pcstore G4_EVERSTONE)


@dataclass
class Option:
    """One party mon that evolves by trade (ok) or would, but can't right now (why)."""
    slot: int
    key: str                 # Soul Link key (unchanged by the evolution)
    dex: int                 # national dex now
    into: int                # national dex after
    name: str                # species name now ("Kadabra")
    into_name: str
    nick: str                # as the game shows it
    item: str | None = None  # held item that gets used up
    ok: bool = True
    why: str = ""            # why it can't: "needs to hold a Metal Coat", "holds an Everstone", ...
    new_nick: str = ""       # after evolve(): the name it has now

    def label(self) -> str:
        """'ROCKY (Onix)' or 'Onix' for an un-nicknamed mon."""
        return self.name if self.nick.upper() == self.name.upper() or not self.nick else f"{self.nick} ({self.name})"


def _clean(s: str, n: int = 12) -> str:
    return "".join(ch for ch in (s or "") if ch.isprintable())[:n]


def _title(s: str) -> str:
    return s.title() if s and s.isupper() else s


def _a(name: str) -> str:
    """'a Metal Coat', 'an Electirizer'."""
    return ("an " if name[:1].upper() in "AEIOU" else "a ") + name


# --- which mons ------------------------------------------------------------------------------------------------
def options(kind: str, mem, rom, sl=None) -> list | None:
    """Party mons that evolve by trade, in party order. kind = "gen1" (Yellow) | "gen2" (Crystal) | "gen4"
    (Platinum; rom = ndsrom.PlatinumData). None while the party can't be read (the game is updating it)."""
    out = {"gen1": _gen1_options, "gen2": _gen2_options, "gen4": _gen4_options}[kind](mem, rom)
    if out is not None and sl is not None:
        for o in out:
            if o.ok and sl.status(o.key) == "dead":
                o.ok, o.why = False, "lost in this run"
    return out


def _gen1_options(mem, rom):
    party = mem.party_checked()
    if party is None:
        return None
    out = []
    for i, m in enumerate(party):
        new = game.TRADE_EVOLUTIONS.get(m.species)
        if not new:
            continue
        out.append(Option(i, game.mon_key(m.data), rom.dex_no(m.species), rom.dex_no(new),
                          _title(rom.species_name(m.species)), _title(rom.species_name(new)), _clean(m.nick_str())))
    return out


def _gen2_options(mem, rom):
    party = mem.party_checked()
    if party is None:
        return None
    out = []
    for i, m in enumerate(party):
        rule = crystal.TRADE_EVOLUTIONS.get(m.species)
        if m.egg or not rule:
            continue
        new, need = rule
        o = Option(i, crystal.mon_key(m.data), m.species, new, _title(rom.species_name(m.species)),
                   _title(rom.species_name(new)), _clean(m.nick_str()), CRYSTAL_ITEMS.get(need) if need else None)
        if m.item == crystal.EVERSTONE:
            o.ok, o.why = False, "holds an Everstone"
        elif need is not None and m.item != need:
            o.ok, o.why = False, "needs to hold " + _a(CRYSTAL_ITEMS.get(need, "special item"))
        out.append(o)
    return out


_G4_CACHE: dict = {}


def g4_trade_evolutions(rom) -> dict:
    """{species: [(target, item or None)]} from the player's ROM (ndsrom.PlatinumData.trade_evolutions), cached."""
    hit = _G4_CACHE.get(id(rom))
    if hit is None or hit[0] is not rom:
        hit = _G4_CACHE[id(rom)] = (rom, rom.trade_evolutions())
    return hit[1]


def _g4_pick(species: int, item: int, evos: dict):
    """(target, needed item or None) the game would evolve into, or (None, why)."""
    rules = evos.get(species) or []
    if item == G4_EVERSTONE:
        return None, "holds an Everstone"
    for target, need in rules:
        if need is None or need == item:
            return (target, need), ""
    return None, "needs"


def _gen4_options(mem, rom):
    from . import pk4, platinum
    party = mem.party_checked()
    if party is None or rom is None:
        return None
    evos = g4_trade_evolutions(rom)
    out = []
    for i, rec in enumerate(party):
        pk = pk4.PK4(pk4.decrypt(rec))
        if pk.species == 0 or pk.is_egg or pk.species not in evos:
            continue
        pick, why = _g4_pick(pk.species, pk.item, evos)
        target, need = pick if pick else (evos[pk.species][0][0], None)
        o = Option(i, platinum.mon_key(rec), pk.species, target, _title(rom.species_name(pk.species)),
                   _title(rom.species_name(target)), _clean(pk.nickname), rom.item_name(need) if need else None)
        if pick is None:
            o.ok = False
            if why == "needs":
                names = [rom.item_name(n) for _t, n in evos[pk.species] if n]
                o.why = "needs to hold " + " or ".join(_a(n) for n in names)
                if len(evos[pk.species]) == 1:
                    o.item = names[0] if names else None
            else:
                o.why = why
        out.append(o)
    return out


# --- the evolution ------------------------------------------------------------------------------------------------
class EvoFailed(Exception):
    """The evolution can't happen (anymore): the text goes to the player and the partner."""


def evolve(kind: str, mem, rom, key: str, sl=None) -> Option:
    """Evolve the party mon with Soul Link key `key` in the game's RAM, like a trade and trade back. Returns the
    Option it was (label() = the mon before, `new_nick` = its name now). Raises EvoFailed if it isn't in the party or
    can't evolve; returns None if the party can't be read right now (try again next frame). Call only while the
    game is safe (and on Platinum when observe_stable() said so). With `sl` (the run's SoulLink) a mon that died
    in the run meanwhile is refused. The EvoFailed text is neutral (the partner sees it too)."""
    opts = options(kind, mem, rom, sl)
    if opts is None:
        return None
    opt = next((o for o in opts if o.key == key), None)
    if opt is None:
        raise EvoFailed("not in the party anymore")
    if not opt.ok:
        raise EvoFailed(opt.why if opt.why.startswith("lost") else f"it {opt.why}")
    if kind == "gen1":
        nick = _evolve_gb(mem, rom, opt, game.evolve_for_trade)
    elif kind == "gen2":
        nick = _evolve_gb(mem, rom, opt, crystal.evolve_for_trade)
    else:
        nick = _evolve_gen4(mem, rom, opt)
    log.info("trade evolution: party slot %d %s -> %s (key %s, item %s)", opt.slot, opt.name, opt.into_name, key,
             opt.item)
    opt.new_nick = nick
    return opt


def _evolve_gb(mem, rom, opt, evolve_fn) -> str:
    party = mem.party_checked()
    m = party[opt.slot]
    new = evolve_fn(m, rom)                    # the normal-trade code: species, stats, HP, item, default name
    if new.species == m.species:
        raise EvoFailed("it can't evolve")
    mem.write_party_slot(opt.slot, new)
    mem.set_dex(new.species)
    return _clean(new.nick_str())


def _evolve_gen4(mem, rom, opt) -> str:
    """Platinum: what the game's evolution does to the party record (pret pokeplatinum: species, the held item used
    up for EVO_TRADE_WITH_HELD_ITEM, ability from the new species' slot PID & 1, the default name if never
    nicknamed, Pokemon_CalcLevelAndStats: stats at the same level, current HP + (new max - old max), 0 stays 0),
    plus Pokedex_Capture for the new species. Same rules as the server's PC trade evolution (pcstore)."""
    from . import pk4, platinum
    from .ndsrom import EOS
    party = mem.party_checked()
    rec = party[opt.slot]
    pk = pk4.PK4(pk4.decrypt(rec))
    pick, _why = _g4_pick(pk.species, pk.item, g4_trade_evolutions(rom))
    if pick is None:
        raise EvoFailed("it can't evolve")
    target, need = pick
    old_max, old_hp = pk.stats[0], pk.hp
    pk.species = target
    if need is not None:
        pk.item = 0                                            # the item is used up
    ab = rom.abilities(target)
    pk.ability = ab[1] if (ab[1] and pk.pid & 1) else ab[0]
    if not pk.has_nickname:
        codes = list(rom.species_name_codes(target))[:10]
        pk.nickname_codes = codes + [EOS] + [0] * (10 - len(codes))
    st = pk4.calc_stats(pk, rom, pk.party_level)
    pk.stats = st
    if old_hp:
        pk.hp = max(1, min(st[0], old_hp + st[0] - old_max))
    new = pk.encrypted()
    mem.m.write(mem.party_addr(opt.slot), new)
    platinum.mark_dex(mem, [new], rom)
    return _clean(pk.nickname)


# --- the flow (mixed into App) --------------------------------------------------------------------------------------
class TradeEvoMixin:
    def init_tradeevo(self):
        self.server_features: set = set()   # welcome.features
        self.evo_out: dict | None = None    # my trade evolution in progress (see SPEC.md section 7)
        self.evo_pick = False                # the Soul Link panel shows the picker
        self.evo_page = 0                    # ...which page of it (small windows)
        self._evo_opts = (-99, None)         # (frame, options) cache for the panel
        self._evo_closed: set = set()        # my evolutions I ended myself: the server's end for them is quiet
        self._evo_help: dict = {}            # evo id -> (name, mon) of a partner's evolution I said yes to

    def open_tradeevo_picker(self):
        self.evo_pick, self.evo_page = True, 0

    def evo_kind(self) -> str:
        return "gen4" if getattr(self, "ds", False) else ("gen2" if self.gen2 else "gen1")

    def evo_rom(self):
        return self.plat_data if getattr(self, "ds", False) else self.rom

    def tradeevo_options(self):
        """The picker's list (re-read a few times a second), None while unreadable."""
        f, opts = self._evo_opts
        if self.frame - f >= 10 or self.frame < f:
            try:
                new = options(self.evo_kind(), self.game, self.evo_rom(), self.sl)
            except Exception:  # noqa: BLE001 - never break the panel over a read
                log.exception("reading trade evolution options failed")
                new = None
            if new is not None or opts is None or self.frame < f:
                opts = new                       # (a frame the party can't be read keeps the last list: no flicker)
            self._evo_opts = (self.frame, opts)
        return opts

    def tradeevo_blocker(self) -> str:
        """'' if a trade evolution can be asked for now, else why not (shown in the panel)."""
        if not self.chal or not self.full:
            return "Only in a Soul Link."
        if CAP not in self.server_features:
            return "The server needs an update for trade evolutions (ask the host)."
        uid, partner = self.chal_partner()
        e = next((r for r in self.roster if r.get("uid") == uid), {}) or {}
        if self.net.status != "online" or self.chal_wait or self.sl is None or e.get("online") is False:
            return f"You both need to be in the run ({partner} too)."
        if CAP not in (e.get("challenge_rules") or []):
            return f"{partner} needs to update YellowLink (press U) to help with trade evolutions."
        if self.evo_out:
            return "A trade evolution is going on."
        return ""

    def request_tradeevo(self, opt: Option):
        why = self.tradeevo_blocker()
        if why:
            self.toast(why, 5, ui.BAD)
            return
        if not opt.ok:
            self.toast(f"{opt.label()} {opt.why}.", 5, ui.BAD)
            return
        if self.referee and self.referee.battle is not None or not self.game.is_safe():
            self.toast("Stand still in the overworld first (no menu, text or battle).", 5, ui.BAD)
            return
        _, partner = self.chal_partner()
        eid = uuid.uuid4().hex[:12]
        self.net.send({"type": "challenge_evo_request", "challenge_id": self.chal["id"], "evo_id": eid,
                       "key": opt.key, "dex": opt.dex, "into": opt.into, "nick": opt.nick, "item": opt.item})
        self.evo_out = {"id": eid, "cid": self.chal["id"], "opt": opt, "state": "asking", "since": time.time(),
                        "unsent": None}
        log.info("asked %s to help trade-evolve %s (%s)", partner, opt.label(), eid)
        self.close_panel()
        self.release_all()
        self.mode = "evo_wait"

    def cancel_tradeevo(self):
        o = self.evo_out
        if not o or o["state"] != "asking":
            return
        self.net.send({"type": "challenge_evo_done", "challenge_id": o["cid"], "evo_id": o["id"], "ok": False,
                       "why": "cancelled"})
        self._evo_closed.add(o["id"])
        self.evo_out = None
        if self.mode == "evo_wait":
            self.mode = "play"
        self.toast("Trade evolution cancelled.", 3, ui.DIM)

    # --- server messages ------------------------------------------------------------------------------------------
    def on_challenge_evo_request(self, msg):
        cid, eid = msg.get("challenge_id"), msg.get("evo_id")
        # (asking ourselves at the same moment is fine: the server took the partner's first and ends ours with
        # "busy" - this question shows once our wait is over)
        if not self.chal or cid != self.chal["id"] or self.prompt:
            msg = {"type": "challenge_evo_respond", "challenge_id": cid, "evo_id": eid, "accept": False}
            if self.chal and cid == self.chal["id"]:
                msg["busy"] = True                       # another question is up: "Alex is busy right now"
            self.net.send(msg)
            return
        self.prompt = {"kind": "tradeevo", "id": eid, "cid": cid, "name": msg.get("name", "?"),
                       "dex": msg.get("dex", 0), "into": msg.get("into", 0), "nick": str(msg.get("nick") or ""),
                       "item": msg.get("item"), "until": time.time() + 55, "shown": None}
        self._maybe_show_prompt()

    def evo_prompt_ready(self) -> bool:
        """Show the partner's question only at a quiet moment (not in a battle, menu or text)."""
        try:
            return bool(self.game.is_safe())
        except Exception:  # noqa: BLE001
            return True

    def respond_tradeevo(self, accept: bool):
        p = self.prompt
        if not p or p.get("kind") != "tradeevo":
            return
        self.net.send({"type": "challenge_evo_respond", "challenge_id": p["cid"], "evo_id": p["id"],
                       "accept": accept})
        if accept:
            name, nick = self.dex_name(p.get("dex", 0)), p.get("nick") or ""
            self._evo_help[p["id"]] = (p["name"], name if not nick or nick.upper() == name.upper() else nick)
            self.toast(f"Helping {p['name']}...", 3, ui.ACCENT)

    def on_challenge_evo_go(self, msg):
        o = self.evo_out
        if o and msg.get("evo_id") == o["id"] and o["state"] == "asking":
            o["state"], o["since"] = "go", time.time()
            o["tick"] = o["since"]

    def on_challenge_evo_end(self, msg):
        eid, ok, reason = msg.get("evo_id"), bool(msg.get("ok")), str(msg.get("reason") or "")
        if eid in self._evo_closed:
            self._evo_closed.discard(eid)                # one I ended myself (and told the player about)
            return
        o = self.evo_out
        if o and eid == o["id"]:
            self.evo_out = None
            if self.mode == "evo_wait":
                self.mode = "play"
            if not ok and o["state"] != "applied" and reason not in ("cancelled", "expired"):
                _, partner = self.chal_partner()
                text = {"declined": f"{partner} said no.", "no answer": f"{partner} didn't answer.",
                        "partner busy": f"{partner} is busy right now - try again in a moment.",
                        "wait a moment": "wait a moment before asking again.",
                        "not both in the run": f"{partner} isn't in the run right now.",
                        "busy": "another one is going on - try again in a moment.",
                        "the run is over": "the run is over.",
                        "session over": "The run was paused.", "timed out": "That took too long."}.get(
                    reason, f"{reason}." if "YellowLink" in reason
                    else f"Couldn't evolve {o['opt'].label()}: {reason}.")
                self.toast("Trade evolution: " + text, 5, ui.BAD)
            return
        p = self.prompt
        if p and p.get("kind") == "tradeevo" and p.get("id") == eid:
            self.prompt = None
            if self.mode == "prompt":
                self.mode = "play"
            if reason == "cancelled":
                self.toast(f"{p['name']} cancelled the trade evolution.", 4, ui.DIM)
            return
        helped = self._evo_help.pop(eid, None)
        if helped and not ok and reason not in ("declined", "cancelled", "no answer", "expired", "session over"):
            name, mon = helped
            self.toast(f"{name}'s {mon} didn't evolve ({reason}).", 5, ui.BAD)

    def tradeevo_event(self, ev, partner):
        """A partner's `evo` ledger event (on_challenge_event): tell the player."""
        nick, dex = str(ev.get("nick") or ""), ev.get("dex", 0)
        name = self.dex_name(dex)
        mon = name if not nick or nick.upper() == name.upper() else nick
        item = f" ({ev['item']} used up)" if ev.get("item") else ""
        self._chal_notify(f"{partner}'s {mon} evolved into {self.dex_name(ev.get('into', 0))}!{item}", "good")

    # --- per frame (from chal_step) -----------------------------------------------------------------------------
    def tradeevo_step(self):
        o = self.evo_out
        if not o or getattr(self, "_chal_leaving", False):
            return
        if o.get("unsent") and self.net.status == "online" and self.net.send(o["unsent"]):
            o["unsent"] = None
        now = time.time()
        if o["state"] == "asking" and now - o["since"] > ASK_WITHIN:
            self._evo_finish(False, "no answer")         # the answer got lost: free the run on the server too
            self.toast("Trade evolution: no answer.", 4, ui.BAD)
            return
        if o["state"] != "go":
            return
        if self.chal_wait or self.chal_popup:
            o["since"] += now - o.get("tick", now)       # a paused run doesn't count against the time limit
            o["tick"] = now
            return
        o["tick"] = now
        if now - o["since"] > APPLY_WITHIN:
            self._evo_finish(False, "no quiet moment")
            self.toast("Couldn't find a quiet moment to evolve - try again standing still.", 5, ui.BAD)
            return
        try:
            if not self.game.is_safe() or (self.referee and self.referee.battle is not None):
                return
            if getattr(self, "ds", False) and not self.game.observe_stable():
                return                                   # Platinum: the game may be working on the party record
            done = evolve(self.evo_kind(), self.game, self.evo_rom(), o["opt"].key, self.sl)
        except EvoFailed as e:
            self._evo_finish(False, str(e)[:30])
            self.toast(f"Couldn't evolve {o['opt'].label()}: {e}.", 5, ui.BAD)
            return
        except Exception:  # noqa: BLE001 - never break the game loop
            log.exception("trade evolution failed")
            self._evo_finish(False, "error")
            return
        if done is None:
            return                                       # party mid-update: next frame
        try:
            self.autosave(force=True)                    # resume = the evolved state, even if the app dies now
        except Exception:  # noqa: BLE001
            log.exception("snapshot after the trade evolution failed")
        self._evo_finish(True)
        self._evo_opts = (-99, None)
        item = f" ({done.item} used up)" if done.item else ""
        self._chal_notify(f"{done.label()} evolved into {done.into_name}!{item}", "good")

    def _evo_finish(self, ok, why=""):
        o = self.evo_out
        msg = {"type": "challenge_evo_done", "challenge_id": o["cid"], "evo_id": o["id"], "ok": ok}
        if why:
            msg["why"] = why
        if ok:
            o["state"] = "applied"                       # kept until the server's end (resend if offline)
            if not self.net.send(msg):
                o["unsent"] = msg
        else:
            self.net.send(msg)
            self._evo_closed.add(o["id"])                # (told the player here; the server's end is quiet)
            self.evo_out = None
        if self.mode == "evo_wait":
            self.mode = "play"

    def tradeevo_reset(self):
        """Back in the run after a reconnect (challenge_state): resend a `done` the server may not have seen (it
        answers "expired" if it already had it); an evolution still asking / at go is cancelled (the question or
        the go may have been lost with the connection)."""
        o = self.evo_out
        if not o:
            return
        if o["state"] == "applied":
            if not o.get("unsent"):
                o["unsent"] = {"type": "challenge_evo_done", "challenge_id": o["cid"], "evo_id": o["id"],
                               "ok": True}
            return
        self._evo_finish(False, "connection lost")
        self.toast("Trade evolution cancelled (connection lost) - ask again.", 4, ui.BAD)
