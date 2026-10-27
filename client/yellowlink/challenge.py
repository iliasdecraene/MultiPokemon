"""Client side of challenges (Soul Link): requests, travelling in/out, waiting/pausing, the referee.

Mixed into App (see app.py). SPEC.md section 7 has the protocol.
"""
from __future__ import annotations

import logging
import time
import uuid

from . import library, ui
from .soullink import Referee, SoulLink

log = logging.getLogger("yellowlink.challenge")

KIND_NAMES = {"soul_link": "Soul Link"}
FINISHED = ("completed", "failed", "abandoned")
CHALLENGE_GAMES = ("yellow", "crystal", "platinum")     # order of the buttons in the new-challenge form
CHALLENGE_RULES = ("exp_share",)        # optional run rules this client applies (`library.challenge_rules`)
CHALLENGE_CAPS = ("trade_evo",)         # ...and what else it can do in a run (same list; SPEC.md section 7)
SINNOH_CAP = "sinnoh_v"                 # + Complete Sinnoh spec version, also in `challenge_rules` (SPEC.md 11)
LEGACY_GAMES = ("yellow", "crystal")    # every challenge client (0.6.0+) plays these; others need `challenge_games`
# What the referee needs from a game adapter's memory class (crystal.py / platinum.py "Soul Link" sections)
REFEREE_HELPERS = ("battle_info", "battle_party_slot", "has_poke_balls", "current_box_records", "move_party_to_box",
                   "move_box_to_party", "daycare_records", "badges")


class ChallengeMixin:
    def init_challenges(self):
        self.challenges: dict = {}           # id -> summary of my challenges (from the server)
        self.chal = self.cfg.challenge       # {"id", "game"} while playing one, else None
        self.chal_rec = None                 # full record (with ledger) while playing
        self.sl: SoulLink | None = None
        self.referee: Referee | None = None
        self.chal_wait = "load" if self.chal else None    # None | "load" | "lost" | "offline"
        self.chal_wait_name = ""
        self.chal_popup = None               # {"title", "lines", "until", "then"}
        self.chal_outbox: list = []          # our rule events not yet confirmed by the server (resent until echoed)
        self.chal_sent: dict = {}            # cid -> time last sent
        self.enter_challenge = None          # set -> main loop restarts into that challenge
        self.leave_challenge = False         # set -> main loop restarts into the main game
        self.chal_form = None                # new-challenge form {"uid", "name", "game", "dupes", "shiny"}
        self.chal_view_uid = None            # whose challenge list the panel shows
        self._chal_leaving = False

    # --- helpers ----------------------------------------------------------------------------------------------
    @property
    def in_challenge(self) -> bool:
        return self.chal is not None

    def chal_partner(self, rec=None):
        rec = rec or self.chal_rec or self.challenges.get((self.chal or {}).get("id"))
        if not rec:
            return None, "your partner"
        uid = next((u for u in rec["players"] if u != self.cfg["uid"]), None)
        return uid, rec.get("names", {}).get(uid, "your partner")

    def challenges_with(self, uid) -> list:
        return sorted((c for c in self.challenges.values() if uid in c.get("players", [])),
                      key=lambda c: c.get("created", 0), reverse=True)

    def open_challenge_with(self, uid, kind="soul_link"):
        return next((c for c in self.challenges_with(uid) if c["kind"] == kind and c["status"] == "active"), None)

    def dex_name(self, dex: int) -> str:
        try:
            if getattr(self, "ds", False):                 # Platinum: national dex = Gen-4 species id
                data = self.plat_data
                return data.species_name(int(dex)).title() if data is not None and 1 <= int(dex) <= 493 \
                    else f"#{dex}"
            if self.gen2:
                return self.rom.species_name(dex).title()
            sp = self.rom.species_for_dex(dex)
            return self.rom.species_name(sp).title() if sp else f"#{dex}"
        except Exception:  # noqa: BLE001
            return f"#{dex}"

    def chal_label(self) -> str:
        return KIND_NAMES.get((self.chal_rec or {}).get("kind", "soul_link"), "Challenge")

    # --- connection ------------------------------------------------------------------------------------------
    def chal_on_connected(self):
        # only games the rules can be enforced in (e.g. not Crystal v1.1); `challenge_games` tells the server which
        # of them this client can run a challenge in (0.7.x clients list Platinum in `games` but have no Platinum
        # Soul Link, so the server only allows Platinum for clients that send it - SPEC.md section 7)
        self.net.send({"type": "library", "games": sorted(g for g in self.lib.games if self._game_supported(g)),
                       "challenge_games": self.challenge_games(),
                       "challenge_rules": list(CHALLENGE_RULES) + list(CHALLENGE_CAPS) + self._sinnoh_caps()})
        if self.chal:
            if self.chal_wait == "offline":
                self.chal_wait = "load"           # back online: wait until the server says both are in
            self.net.send({"type": "challenge_join", "challenge_id": self.chal["id"]})

    def chal_on_disconnected(self):
        if self.chal and not self._chal_leaving and self.chal_wait != "load":
            self.chal_wait = "offline"

    # --- server messages -----------------------------------------------------------------------------------
    def on_challenge_list(self, msg):
        self.challenges = {c["id"]: c for c in msg.get("challenges", []) if isinstance(c, dict) and "id" in c}

    def on_challenge_update(self, msg):
        c = msg.get("challenge") or {}
        if "id" not in c:
            return
        self.challenges[c["id"]] = c
        if self.chal and c["id"] == self.chal["id"]:
            if self.chal_rec:
                self.chal_rec.update(c)
            if c["status"] in FINISHED and not self._chal_leaving:
                _, partner = self.chal_partner(c)
                if c["status"] == "completed":
                    self.chal_end_popup("Soul Link complete!",
                                        [f"You and {partner} both made it to the Hall of Fame.",
                                         ("The badge is yours - see Multiplayer > Challenges.", ui.GOOD)])
                elif c["status"] == "failed":
                    self.chal_end_popup("Soul Link failed", [c.get("reason", "") + ".",
                                                             ("Better luck next run!", ui.DIM)])
                else:
                    self.chal_end_popup("Soul Link abandoned", [c.get("reason", "") + "."])

    def on_challenge_request(self, msg):
        busy = self.chal or self.trade or self.battle or self.live or self.prompt
        game = msg.get("game", "yellow")
        playable = game in self.challenge_games()
        if busy or not playable:
            self.net.send({"type": "challenge_respond", "request_id": msg.get("request_id"), "accept": False})
            if not playable:
                info = library.GAMES_BY_ID.get(game)
                self.toast(f"{msg.get('name')} invited you to a Soul Link in {info.title if info else game}, "
                           f"but you don't have that game added (Regions; Crystal needs v1.0).", 8, ui.BAD)
            return
        self.prompt = {"kind": "challenge", "id": msg["request_id"], "name": msg.get("name", "?"),
                       "until": time.time() + 55, "shown": None, "game": game,
                       "rules": msg.get("rules") or {}, "continue": msg.get("challenge_id")}
        self._maybe_show_prompt()

    def respond_challenge(self, accept: bool):
        p = self.prompt
        if not p or p.get("kind") != "challenge":
            return
        self.net.send({"type": "challenge_respond", "request_id": p["id"], "accept": accept})
        self.prompt = None
        if self.mode == "prompt":
            self.mode = "play"
        if accept:
            self.toast("Get ready...", 3, ui.ACCENT)

    def on_challenge_declined(self, msg):
        reason = {"declined": "declined", "no answer": "didn't answer", "busy": "is busy"}.get(msg.get("reason"),
                                                                                               msg.get("reason"))
        self.toast(f"{msg.get('name', 'They')} {reason}.", 5, ui.BAD)

    def on_challenge_go(self, msg):
        c = msg.get("challenge") or {}
        if "id" not in c:
            return
        self.challenges[c["id"]] = c
        if c.get("game") not in self.challenge_games():
            self.net.send({"type": "challenge_leave", "challenge_id": c["id"], "reason": "game missing"})
            self.toast("You don't have that game added (Regions; Crystal needs v1.0).", 6, ui.BAD)
            return
        if c.get("events", 0) > 0 and not self.has_challenge_save(c["id"]):
            # continuing a run whose save isn't on this computer would start over and kill everything
            self.net.send({"type": "challenge_leave", "challenge_id": c["id"], "reason": "save missing"})
            self.chal_end_popup_home("Can't continue here", [
                "The save for this Soul Link isn't on this computer.",
                ("Continue it from the computer you started it on.", ui.DIM)])
            return
        if self.chal:
            return
        self.close_panel()
        # the run's rules travel with it (Exp Share All applies from the first frame, before the server's record)
        self.enter_challenge = {"id": c["id"], "game": c["game"], "rules": dict(c.get("rules") or {})}
        _, partner = self.chal_partner(c)
        self.cfg["_travel_msg"] = f"Starting your Soul Link with {partner}..."
        self.quit_requested = True
        log.info("entering challenge %s (%s)", c["id"], c["game"])

    def on_challenge_state(self, msg):
        rec = msg.get("challenge") or {}
        if not self.chal or rec.get("id") != self.chal["id"]:
            return
        self.chal_rec = rec
        self.challenges[rec["id"]] = {k: v for k, v in rec.items() if k != "ledger"}
        partner, _ = self.chal_partner(rec)
        sl = SoulLink(self.cfg["uid"], partner, rec.get("rules"), rec.get("ledger", []))
        logged = {e.get("cid") for e in rec.get("ledger", []) if e.get("p") == self.cfg["uid"]}
        self.chal_outbox = [ev for ev in self.chal_outbox if ev.get("cid") not in logged]
        self.chal_sent.clear()                         # resend everything not in the server's log yet
        for ev in self.chal_outbox:
            sl.local(ev)
        self.sl = sl
        if hasattr(self, "tradeevo_reset"):
            self.tradeevo_reset()                      # resend a trade evolution's `done` the server may not have
        if self.referee:
            self.referee.sl = sl
        elif self.full and hasattr(self.game, "battle_info"):
            from .challenge_view import ChallengeView
            self.referee = Referee(sl, ChallengeView(self), self._chal_send, self._chal_notify)
            self.referee.progress = self._chal_progress
        self._chal_flush()

    def on_challenge_ready(self, msg):
        if not self.chal or msg.get("challenge_id") != self.chal["id"]:
            return
        was = self.chal_wait
        self.chal_wait = None
        _, partner = self.chal_partner()
        if was == "lost":
            self.toast(f"{partner} is back!", 3, ui.GOOD)
        elif was:
            self.toast(f"{partner} is here - good luck!", 4, ui.GOOD)

    def on_challenge_partner_lost(self, msg):
        if self.chal and msg.get("challenge_id") == self.chal["id"]:
            self.chal_wait = "lost"
            self.chal_wait_name = msg.get("name", "")
            self.chal_wait_since = time.time()

    def on_challenge_partner_left(self, msg):
        cid = msg.get("challenge_id")
        if self.enter_challenge and self.enter_challenge.get("id") == cid:
            self.enter_challenge = None
            self.quit_requested = False
            self.toast(f"{msg.get('name')} left before it started.", 5, ui.BAD)
            return
        if not self.chal or cid != self.chal["id"] or self._chal_leaving:
            return
        reason = msg.get("reason", "left")
        why = {"disconnected": "lost their connection", "closed the game": "closed the game",
               "paused": "paused the challenge", "didn't load in": "didn't load in", "abandoned": "abandoned it",
               "travelled": "went back to their adventure",
               "save missing": "doesn't have this run's save on their computer",
               "game missing": "doesn't have the game for this run"}.get(reason, reason)
        self.chal_end_popup(f"{msg.get('name', 'Your partner')} left",
                            [f"{msg.get('name', 'Your partner')} {why}.",
                             ("Taking you back to where you left off in your main game.", ui.DIM),
                             ("Your Soul Link is saved - continue it together any time.", ui.DIM)])

    def on_challenge_event(self, msg):
        if not self.sl or not self.chal or msg.get("challenge_id") != self.chal["id"]:
            return
        ev = msg.get("event") or {}
        self.sl.add(ev)
        if ev.get("p") == self.cfg["uid"]:
            cid = ev.get("cid")
            if cid:
                self.chal_outbox = [e for e in self.chal_outbox if e.get("cid") != cid]
                self.chal_sent.pop(cid, None)
            return
        _, partner = self.chal_partner()
        area = self.referee.view.area_name(ev.get("area", "")) if self.referee and ev.get("area") else ""
        if ev.get("t") == "enc" and ev.get("status") == "caught":
            self.toast(f"{partner} caught a {self.dex_name(ev.get('dex', 0))}"
                       + (f" in {area}" if area and not ev.get("gift") else "") + "!", 5, ui.GOOD)
        elif ev.get("t") == "enc" and ev.get("status") == "failed" and not ev.get("auto"):
            self.toast(f"{partner} missed their encounter in {area}.", 5, ui.BAD)
        elif ev.get("t") == "hof":
            self.toast(f"{partner} entered the Hall of Fame!", 6, ui.GOOD)
        elif ev.get("t") == "evo" and hasattr(self, "tradeevo_event"):
            self.tradeevo_event(ev, partner)

    # --- referee callbacks ---------------------------------------------------------------------------------
    def _chal_send(self, ev):
        ev = dict(ev)
        if not ev.get("cid"):
            ev["cid"] = uuid.uuid4().hex[:10]          # every queued event needs its own id (resend/dedupe)
        self.chal_outbox.append(ev)
        self._chal_flush()

    def _chal_flush(self):
        """Send rule events; keep each until the server echoes it (a silent connection drop loses the queue)."""
        if not self.chal or self.net.status != "online" or self.chal_wait == "offline" or self.sl is None:
            return
        now = time.time()
        for ev in self.chal_outbox:
            last = self.chal_sent.get(ev.get("cid"))
            if last is not None and now - last < 10:
                continue
            if not self.net.send({"type": "challenge_event", "challenge_id": self.chal["id"], "event": ev}):
                return
            self.chal_sent[ev.get("cid")] = now

    def _chal_notify(self, text, kind="info"):
        color = {"good": ui.GOOD, "bad": ui.BAD}.get(kind, ui.ACCENT)
        self.toast(text, 6, color)
        self.sys_msg(text, color)

    def _chal_progress(self, badges):
        if self.chal:
            self.net.send({"type": "challenge_progress", "challenge_id": self.chal["id"], "badges": badges})

    # --- per frame -----------------------------------------------------------------------------------------
    def chal_paused(self) -> bool:
        """Freeze the game while waiting for the partner (or for our own connection)."""
        if not self.chal:
            return False
        if self.net.status != "online" and not self._chal_leaving and self.chal_wait != "load":
            self.chal_wait = "offline"
        return bool(self.chal_wait or self.chal_popup)

    def chal_step(self):
        if self.chal_popup and time.time() > self.chal_popup["until"]:
            self.chal_close_popup()
        if self.chal_outbox and self.frame % 120 == 0:
            self._chal_flush()
        if self.chal and self.referee and not self.chal_wait and not self.chal_popup:
            try:
                if getattr(self, "ds", False):
                    self._chal_pc_poll()
                self.referee.step()
            except Exception:  # noqa: BLE001 - never stop the game over a rules bug
                log.exception("soul link referee failed")
        if self.chal and hasattr(self, "tradeevo_step"):
            self.tradeevo_step()

    def _chal_pc_poll(self):
        """Platinum has no PC hook in a challenge (the PC database is off there): the box UI running = PC open."""
        try:
            now = bool(self.game.pc_open())
        except Exception:  # noqa: BLE001
            return
        if now != self.referee.pc_open:
            if now:
                self.referee.on_pc_open()
            else:
                self.referee.on_pc_close()

    # --- leaving -------------------------------------------------------------------------------------------
    def chal_end_popup(self, title, lines):
        self.release_all()
        self.close_panel()
        self.chal_popup = {"title": title, "lines": lines, "until": time.time() + 12}
        self.mode = "chal_popup"

    def chal_close_popup(self):
        self.chal_popup = None
        if self.mode == "chal_popup":
            self.mode = "play"
        if self.chal:
            self.leave_to_main(notify=False)

    def leave_to_main(self, reason="paused", notify=True, travel_to=None):
        """Save the challenge and go back to the main game (or to `travel_to`)."""
        if not self.chal or self._chal_leaving:
            return
        self._chal_leaving = True
        if notify:
            self.net.send({"type": "challenge_leave", "challenge_id": self.chal["id"], "reason": reason})
        self.leave_challenge = True
        self.travel_to = travel_to
        main = self.lib.info(travel_to or self.lib.current or "yellow")
        self.cfg["_travel_msg"] = f"Back to {main.title}..."
        self.quit_requested = True
        log.info("leaving challenge %s (%s)", self.chal["id"], reason)

    def chal_shutdown(self):
        """App is closing while in a challenge: tell the partner right away (no 30 s wait)."""
        if self.chal and not self._chal_leaving:
            self._chal_leaving = True
            self.net.send({"type": "challenge_leave", "challenge_id": self.chal["id"], "reason": "closed the game"})

    # --- requests from the panel ------------------------------------------------------------------------------
    def open_challenges(self, uid):
        self.chal_view_uid = uid
        self.chal_form = None
        self.panel = "challenges"
        self.mode = "panel"

    def new_challenge_form(self, uid, name):
        shared = self.shared_games(uid)
        self.chal_form = {"uid": uid, "name": name, "game": shared[0] if shared else None,
                          "dupes": True, "shiny": True,
                          # Exp Share All for both: starts as my own setting, if the partner's client knows the rule
                          "exp_share": bool(self.cfg.get("exp_share_all", True)) and self.can_exp_share_rule(uid)}

    def can_exp_share_rule(self, uid) -> bool:
        """The partner's client (and the server) know the Exp Share All run rule (roster `challenge_rules`)."""
        e = next((r for r in self.roster if r.get("uid") == uid), {}) or {}
        return "exp_share" in (e.get("challenge_rules") or [])

    def toggle_exp_share_rule(self):
        f = self.chal_form
        if not f:
            return
        if not f.get("exp_share") and not self.can_exp_share_rule(f["uid"]):
            e = next((r for r in self.roster if r.get("uid") == f["uid"]), {}) or {}
            self.toast("The server needs updating for this rule (ask the host)." if "challenge_rules" not in e
                       else f"{f['name']} needs to update YellowLink (press U) for Exp Share All in a run.", 5,
                       ui.BAD)
            return
        f["exp_share"] = not f.get("exp_share")

    def chal_exp_share(self):
        """The run's Exp Share All rule (both players) while in a run, else None (= the player's own setting).
        Runs from before the rule existed have none: off, the original games' rule."""
        if not self.chal:
            return None
        rec = self.chal_rec if self.chal_rec and self.chal_rec.get("id") == self.chal["id"] else None
        rules = (rec or {}).get("rules")
        if rules is None:
            rules = self.chal.get("rules")
        if rules is None:
            rules = (self.challenges.get(self.chal["id"]) or {}).get("rules")
        return bool((rules or {}).get("exp_share", False))

    def shared_games(self, uid) -> list:
        """Games both of us can run a Soul Link in (form buttons). Yellow/Crystal: the partner's `games` (None =
        unknown, allowed as before); Platinum also needs it in their `challenge_games` (clients 0.7.4+)."""
        e = next((r for r in self.roster if r.get("uid") == uid), {}) or {}
        theirs = e.get("games")
        their_chal = e.get("challenge_games") or []
        out = []
        for g in self.challenge_games():
            if g in LEGACY_GAMES:
                ok = theirs is None or g in theirs
            else:
                ok = g in their_chal and (theirs is None or g in theirs)
            if g == "platinum" and ok:
                # both must play the same Sinnoh: the same Complete Sinnoh version (or both plain Platinum)
                mine = self._sinnoh_caps()
                ok = sorted(c for c in (e.get("challenge_rules") or []) if c.startswith(SINNOH_CAP)) == mine
            if ok:
                out.append(g)
        return out

    def _sinnoh_caps(self) -> list:
        """["sinnoh_v<spec version>"] when this client plays Complete Sinnoh (sent in `library.challenge_rules`, so
        a Platinum Soul Link is only offered between players with the same wild Pokémon), else []."""
        if not getattr(self, "complete_sinnoh", False):
            return []
        try:
            from . import platinum
            v = platinum.variant_of(getattr(self, "rom_sha1", ""))
        except Exception:  # noqa: BLE001
            return []
        return [f"{SINNOH_CAP}{v.spec_version}"] if v and v.spec_version is not None else []

    def challenge_games(self) -> list:
        """Games this client can run a challenge in: added, the verified ROM, and an adapter with the referee's
        memory helpers (Platinum: platinum.py's Soul Link section)."""
        return [g for g in CHALLENGE_GAMES if g in self.lib.games and self._game_supported(g)
                and self._referee_ready(g)]

    @staticmethod
    def _referee_ready(game_id) -> bool:
        if game_id in LEGACY_GAMES:
            return True
        if game_id == "platinum":
            try:
                from . import platinum
            except Exception:  # noqa: BLE001 - adapter missing/broken: no Platinum challenges
                return False
            pm = getattr(platinum, "PlatinumMemory", None)
            return pm is not None and all(hasattr(pm, n) for n in REFEREE_HELPERS) \
                and hasattr(platinum, "area_of") and hasattr(platinum, "area_name")
        return False

    def _game_supported(self, game_id) -> bool:
        """The exact ROM the rules were verified on (hashing a ROM is slow: cached per path)."""
        info = library.GAMES_BY_ID.get(game_id)
        path = self.lib.rom_path(game_id)
        if not info or not path:
            return False
        cache = self.__dict__.setdefault("_sup_cache", {})
        if path not in cache:
            try:
                cache[path] = library.KNOWN_GAMES.get(library.sha1_file(path), info).full_support
            except OSError:
                return False
        return cache[path]

    def send_challenge(self):
        f = self.chal_form
        if not f or not f.get("game"):
            return
        self.net.send({"type": "challenge_request", "to": f["uid"], "kind": "soul_link", "game": f["game"],
                       "rules": {"dupes": f["dupes"], "shiny": f["shiny"], "exp_share": bool(f.get("exp_share"))}})
        self.toast(f"Soul Link invite sent to {f['name']}...", 4)
        self.chal_form = None
        self.close_panel()

    def has_challenge_save(self, cid) -> bool:
        d = self.cfg.challenge_dir(cid)
        return (d / "game.sav").exists() or (d / "resume.state").exists()

    def chal_end_popup_home(self, title, lines):
        """A pop-up shown in the main game (nothing to leave)."""
        self.release_all()
        self.close_panel()
        self.chal_popup = {"title": title, "lines": lines, "until": time.time() + 12}
        self.mode = "chal_popup"

    def continue_challenge(self, c):
        if c.get("game") not in self.challenge_games():
            self.toast("You need that game added (Regions; Crystal needs v1.0).", 5, ui.BAD)
            return
        if not self.has_challenge_save(c["id"]):
            self.toast("This run's save isn't on this computer - continue from the one you started on.", 6,
                       ui.BAD)
            return
        uid, name = self.chal_partner(c)
        self.net.send({"type": "challenge_request", "to": uid, "challenge_id": c["id"]})
        self.toast(f"Asked {name} to continue your Soul Link...", 4)
        self.close_panel()

    def abandon_challenge(self, c):
        self.net.send({"type": "challenge_abandon", "challenge_id": c["id"]})
        self.toast("Soul Link abandoned.", 4, ui.BAD)
