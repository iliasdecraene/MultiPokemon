"""Trades and gifts between PC databases (never the party). Mixed into App; protocol in SPEC.md section 2.

The server swaps the entries in both players' stored PCs in one step and sends each player a delta
(`pc_trades`), which PCSync.apply_trade applies to the local store. Works across games: a Pokémon a game can't
hold shows up there as a ?NAME stand-in and stays in the PC.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

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
    my_entry: dict | None = None
    their_entry: dict | None = None
    me_confirmed: bool = False
    them_confirmed: bool = False

    @property
    def receiving_only(self) -> bool:
        return self.gift and not self.giving

    def can_confirm(self) -> bool:
        if self.gift:
            return bool(self.my_entry if self.giving else self.their_entry)
        return bool(self.my_entry and self.their_entry)


class TradeMixin:
    def init_trades(self):
        self.trade: TradeSession | None = None
        self.trade_sel = 0               # highlighted row in "Your PC"
        self.trade_scroll = 0
        self.trade_per = 8               # rows visible (set by the window)

    # --- helpers ---------------------------------------------------------------------------------------------
    def trade_entries(self) -> list:
        return self.pcsync.offerable()

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
        if t and t.trade_id == msg.get("trade_id") and isinstance(msg.get("entry"), dict):
            t.their_entry = msg["entry"]

    def on_trade_confirm_state(self, msg):
        t = self.trade
        if t and t.trade_id == msg.get("trade_id"):
            t.me_confirmed, t.them_confirmed = bool(msg.get("you")), bool(msg.get("them"))

    def offer_entry(self, entry):
        t = self.trade
        if not t or t.receiving_only:
            return
        if entry["id"] not in {e["id"] for _, e in self.trade_entries()}:
            self.toast("That Pokémon isn't available right now.", 3, ui.BAD)
            return
        t.my_entry = entry
        t.me_confirmed = t.them_confirmed = False
        self.net.send({"type": "trade_offer", "trade_id": t.trade_id, "entry_id": entry["id"]})

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
        got, gave, evolved = msg.get("received"), msg.get("gave"), msg.get("evolved")
        if got:
            text = f"You got {got.get('nick') or self.dex_name(got.get('dex', 0))}! It's in your PC."
            if evolved:
                text = f"Your new Pokémon evolved into {evolved.title()}! It's in your PC."
            note = self.entry_note(got)
            if note:
                text += f" ({note[0]}.)"
        else:
            text = f"You gave {(gave or {}).get('nick', 'your Pokémon')} to {t.partner_name if t else 'your friend'}."
        if t and t.trade_id == msg.get("trade_id"):
            self._end_trade(text, ui.GOOD)
        else:
            self.toast(text, 6, ui.GOOD)
            self.sys_msg(text, ui.GOOD)

    def on_trade_cancelled(self, msg):
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
    def on_pc_trades(self, msg):
        changed = False
        for d in msg.get("deltas") or []:
            if isinstance(d, dict) and self.pcsync.apply_trade(d):
                changed = True
        # upload so the server knows these trades arrived (it refuses uploads that miss one)
        self.net.send({"type": "pc_put", "store": self.pcsync.committed})
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
