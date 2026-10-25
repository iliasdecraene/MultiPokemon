"""Trades and gifts between PC databases (never the party). Mixed into App; protocol in SPEC.md section 2.

The server swaps the entries in both players' stored PCs in one step and sends each player a delta
(`pc_trades`), which PCSync.apply_trade applies to the local store. Works across games: a Pokémon a game can't
hold shows up there as a ?NAME stand-in and stays in the PC.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

import pygame

from . import ui

log = logging.getLogger("yellowlink.trades")


@dataclass
class TradeSession:
    trade_id: str
    partner_uid: str
    partner_name: str
    gift: bool = False
    giving: bool = False          # gift: True on the giver's side
    my_entries: list = field(default_factory=list)      # what I offer (several allowed)
    their_entries: list = field(default_factory=list)
    me_confirmed: bool = False
    them_confirmed: bool = False

    @property
    def receiving_only(self) -> bool:
        return self.gift and not self.giving

    def can_confirm(self) -> bool:
        if self.gift:
            return bool(self.my_entries if self.giving else self.their_entries)
        return bool(self.my_entries and self.their_entries)

    def offered(self, entry_id: str) -> bool:
        return any(e["id"] == entry_id for e in self.my_entries)


class TradeMixin:
    def init_trades(self):
        self.trade: TradeSession | None = None
        self.trade_sel = 0               # highlighted row in "Your PC"
        self.trade_scroll = 0
        self.trade_per = 8               # rows visible (set by the window)

    # --- helpers ---------------------------------------------------------------------------------------------
    MAX_OFFER = 20

    def trade_entries(self) -> list:
        """Your PC as the trade window lists it: everything offerable plus what this trade already offers."""
        ok = {e["id"] for _, e in self.pcsync.offerable()}
        if self.trade:
            ok |= set(self.pcsync.locks.get(self.trade.trade_id, []))
        rows = [(bi, e) for bi, box in enumerate(self.pcsync.committed["boxes"]) for e in box if e["id"] in ok]
        return rows

    def entry_label(self, e: dict) -> str:
        species = self.dex_name(e.get("dex", 0))
        nick = e.get("nick") or species
        if species.startswith("#") or nick.upper() == species.upper():   # unknown name in this game / no nickname
            return f"{nick.title() if nick.isupper() else nick} Lv{e.get('level', '?')}"
        return f"{nick} ({species}) Lv{e.get('level', '?')}"

    def entry_note(self, e: dict) -> tuple[str, tuple] | None:
        """A warning if this Pokémon can't be used in the game you're playing (it stays in the PC as ?NAME)."""
        codec = self.pc_codec
        if codec is None:
            return None
        try:
            if codec.is_standin(codec.record_for(e)):
                game = self.game_info.title.replace("Pokémon ", "")
                return f"Not usable in {game} - stays in the PC", ui.ACCENT
        except Exception:  # noqa: BLE001 - bad data from a friend must not break the window
            return "Unreadable Pokémon data", ui.BAD
        return None

    def _trade_blocked(self, e, gift=False) -> str | None:
        if self.chal:
            return "Not during a Soul Link - it's just you and your partner."
        if not e.get("online"):
            return "They need to be online."
        if self.trade or self.battle or self.live:
            return "Finish what you're doing first."
        if self.pcsync.session_open:
            return "Close your in-game PC first."
        if not self.trade_entries():
            return "Your PC is empty - trades use Pokémon from your PC (deposit one first, then wait a few seconds)."
        return None

    # --- requests --------------------------------------------------------------------------------------------
    def action_trade(self, e, gift=False):
        why = self._trade_blocked(e, gift)
        if why:
            self.toast(why, 5, ui.BAD)
            return
        self.net.send({"type": "trade_request", "to": e["uid"], "gift": gift})
        self.toast(f"{'Gift offer' if gift else 'Trade request'} sent to {e.get('name')}...")
        self.close_panel()
        if self.mode == "menu":
            self.mode = "play"

    def action_gift(self, e):
        self.action_trade(e, gift=True)

    def on_trade_request(self, msg):
        if (self.chal or self.trade or self.battle or self.live or self.prompt or self.pcsync.session_open):
            self.net.send({"type": "trade_respond", "trade_id": msg["trade_id"], "accept": False})
            return
        self.prompt = {"kind": "gift" if msg.get("gift") else "trade", "id": msg["trade_id"],
                       "name": msg.get("name", "?"), "until": time.time() + 55, "shown": None}
        self._maybe_show_prompt()

    def on_trade_started(self, msg):
        if self.battle or self.live or self.chal or self.pcsync.session_open:
            self.net.send({"type": "trade_cancel", "trade_id": msg["trade_id"]})
            self.toast("Trade cancelled: close your in-game PC (or finish your battle) first.", 5, ui.BAD)
            return
        me = self.cfg["uid"]
        self.trade = TradeSession(msg["trade_id"], msg["with"], msg.get("name", "?"), gift=bool(msg.get("gift")),
                                  giving=msg.get("giver") == me)
        self.trade_sel = self.trade_scroll = 0
        self.close_panel()
        self.mode = "trade"
        self.release_all()          # the trade window takes the keyboard: the game (and its PC) can't be used

    def on_trade_offer(self, msg):
        t = self.trade
        if not t or t.trade_id != msg.get("trade_id"):
            return
        entries = msg.get("entries")
        if not isinstance(entries, list):                 # 0.6.1 server: one Pokémon
            entries = [msg["entry"]] if isinstance(msg.get("entry"), dict) else []
        t.their_entries = [e for e in entries if isinstance(e, dict)]

    def on_trade_confirm_state(self, msg):
        t = self.trade
        if t and t.trade_id == msg.get("trade_id"):
            t.me_confirmed, t.them_confirmed = bool(msg.get("you")), bool(msg.get("them"))

    def offer_entry(self, entry):
        """Click / Enter on a Pokémon in your PC: add it to your offer, or take it back out."""
        t = self.trade
        if not t or t.receiving_only:
            return
        if entry["id"] not in {e["id"] for _, e in self.trade_entries()}:
            self.toast("That Pokémon isn't available right now.", 3, ui.BAD)
            return
        if t.offered(entry["id"]):
            chosen = [e for e in t.my_entries if e["id"] != entry["id"]]
        elif len(t.my_entries) >= self.MAX_OFFER:
            self.toast(f"At most {self.MAX_OFFER} Pokémon at once.", 3, ui.BAD)
            return
        else:
            chosen = t.my_entries + [entry]
        self._send_offer(chosen)

    def select_box(self, box_index: int):
        """Add every available Pokémon of one PC box to the offer (up to the limit)."""
        t = self.trade
        if not t or t.receiving_only:
            return
        chosen = list(t.my_entries)
        for bi, e in self.trade_entries():
            if bi == box_index and not t.offered(e["id"]) and len(chosen) < self.MAX_OFFER:
                chosen.append(e)
        if len(chosen) != len(t.my_entries):
            self._send_offer(chosen)

    def clear_offer(self):
        if self.trade and self.trade.my_entries:
            self._send_offer([])

    def _send_offer(self, chosen):
        t = self.trade
        t.my_entries = chosen
        t.me_confirmed = t.them_confirmed = False
        # offered Pokémon stay in the PC until we know how the trade ended
        self.pcsync.lock(t.trade_id, [e["id"] for e in chosen])
        self.net.send({"type": "trade_offer", "trade_id": t.trade_id, "entry_ids": [e["id"] for e in chosen]})

    def confirm_trade(self):
        t = self.trade
        if t and t.can_confirm() and not t.me_confirmed:
            self.net.send({"type": "trade_confirm", "trade_id": t.trade_id})

    def cancel_trade(self):
        t = self.trade
        if t:
            self.net.send({"type": "trade_cancel", "trade_id": t.trade_id})
            self._end_trade("Trade cancelled.", ui.DIM)

    def on_trade_complete(self, msg):
        t = self.trade
        got = msg.get("received_all")
        gave = msg.get("gave_all")
        evolved = msg.get("evolved_all")
        if got is None:                                     # 0.6.1 server
            got = [msg["received"]] if msg.get("received") else []
            gave = [msg["gave"]] if msg.get("gave") else []
            evolved = [msg["evolved"]] if msg.get("evolved") else []
        friend = t.partner_name if t else "your friend"
        if got:
            text = (f"You got {self.mons_text(got)}! " + ("It's" if len(got) == 1 else "They're") + " in your PC.")
            if evolved:
                text += " Evolved on arrival: " + ", ".join(n.title() for n in evolved) + "!"
            notes = [e for e in got if self.entry_note(e)]
            if notes:
                game = self.game_info.title.replace("Pokémon ", "")
                text += f" ({len(notes)} can't be used in {game} - they stay in the PC.)" if len(notes) > 1 \
                    else f" ({self.entry_note(notes[0])[0]}.)"
        else:
            text = f"You gave {self.mons_text(gave or [])} to {friend}."
        if t and t.trade_id == msg.get("trade_id"):
            self._end_trade(text, ui.GOOD)
        else:
            self.toast(text, 6, ui.GOOD)
            self.sys_msg(text, ui.GOOD)

    def mons_text(self, entries) -> str:
        names = [e.get("nick") or self.dex_name(e.get("dex", 0)) for e in entries]
        names = [n.title() if n.isupper() else n for n in names]
        if not names:
            return "nothing"
        if len(names) == 1:
            return names[0]
        if len(names) <= 3:
            return ", ".join(names[:-1]) + " and " + names[-1]
        return f"{names[0]}, {names[1]} and {len(names) - 2} more"

    def on_trade_cancelled(self, msg):
        self.pcsync.unlock(msg.get("trade_id"))           # the server says it didn't happen
        t = self.trade
        if not t or t.trade_id != msg.get("trade_id"):
            if not t:
                self.toast("Trade request ended: " + msg.get("reason", ""), 4, ui.DIM)
            return
        self._end_trade("Trade cancelled: " + msg.get("reason", ""), ui.BAD)

    def _end_trade(self, s, color):
        self.trade = None
        if self.mode == "trade":
            self.mode = "play"
        self.toast(s, 6, color)
        self.sys_msg(s, color)

    # --- the PC database side ------------------------------------------------------------------------------
    def on_trade_status(self, msg):
        """Answer to our question about a trade we lost track of (connection dropped mid-trade)."""
        tid, state = msg.get("trade_id"), msg.get("state")
        if state == "failed":
            self.pcsync.unlock(tid)
        elif state == "complete":
            self.net.send({"type": "pc_get"})               # the delta comes with it and releases the lock
        elif state == "pending":
            self._status_retry = time.time() + 5

    def trades_step(self):
        if getattr(self, "_status_retry", 0) and time.time() > self._status_retry and self.net.status == "online":
            self._status_retry = 0
            for tid in list(self.pcsync.locks):
                if not self.trade or self.trade.trade_id != tid:
                    self.net.send({"type": "trade_status", "trade_id": tid})

    def on_pc_trades(self, msg):
        changed = False
        for d in msg.get("deltas") or []:
            if isinstance(d, dict) and self.pcsync.apply_trade(d):
                changed = True
        # upload so the server knows these trades arrived (it refuses uploads that miss one)
        self.pc_push()
        if changed:
            log.info("PC trades applied; store rev %s", self.pcsync.committed.get("rev"))

    # --- keys (the trade window owns the keyboard) ----------------------------------------------------------
    def trade_key(self, k):
        t = self.trade
        if not t:
            return
        rows = self.trade_entries()
        per = getattr(self, "trade_per", 8)
        if k in (pygame.K_UP, pygame.K_DOWN):
            step = -1 if k == pygame.K_UP else 1
            self.trade_sel = max(0, min(max(0, len(rows) - 1), self.trade_sel + step))
            if self.trade_sel < self.trade_scroll:
                self.trade_scroll = self.trade_sel
            elif self.trade_sel >= self.trade_scroll + per:
                self.trade_scroll = self.trade_sel - per + 1
        elif k in (pygame.K_RETURN, pygame.K_KP_ENTER) and rows and not t.receiving_only:
            self.offer_entry(rows[min(self.trade_sel, len(rows) - 1)][1])
        elif k == pygame.K_c:
            self.confirm_trade()
        elif k == pygame.K_ESCAPE:
            self.cancel_trade()
