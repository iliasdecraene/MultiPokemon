"""Back to my last in-game save" (Settings / F7) and what happens whenever a game comes back from its battery save
(that button, the game's own soft reset + CONTINUE, a start without resume snapshot). See SPEC.md section 5.

The app normally resumes from its own snapshot (every 30 s + on close). This button throws that snapshot away (a
copy goes to saves/backups/) and restarts the game from its in-game save, straight into CONTINUE, through the
same restart path as travelling. The YellowLink PC is never rolled back - it's shared with the other games, the
server and friends - so the game is brought in line with it instead:
  * the button refuses while a Pokémon went into or out of this game's PC (or was traded/gifted away) since the
    last in-game save: the save would bring back a copy, or lose one;
  * on every load from the save, pcdb.PCSync.rebase() makes the PC's last view match the save's boxes (mons that
    left the PC never come back from an old box) and party mons put into the PC after that save (a manual soft
    reset can't be refused) are taken out of the party again - the PC copy stays, like PC clone protection does.
"""
from __future__ import annotations

import hashlib
import logging
import os
import time

import pygame

from . import ui

log = logging.getLogger("yellowlink.reload")

GB_BANK = 0x2000
FP_CHECK_EVERY = 10           # frames between battery checks while the confirmation is open
STABLE_FRAMES = 60            # the save must not have changed for this long (the game isn't writing it right now)
RESUME_BACKUPS = 5
# The Game Boy games keep their WRAM through a soft reset (in_game() stays True), so the load from the save is seen
# by an exec hook on the game's CONTINUE: Yellow MainMenu.pressedA (01:5C83, A on the CONTINUE info card - the
# main menu has already loaded the save into WRAM), Crystal Continue (01:5D65, loads the save). pret symbols,
# exercised in dev/e2e/reload_save.py. Platinum: in_game() (the FieldSystem) goes away and comes back.
CONTINUE_HOOKS = {"yellow": (0x01, 0x5C83), "crystal": (0x01, 0x5D65)}


# --- the in-game save, as seen in the battery (App._read_sram bytes) --------------------------------------------
def _blank(data: bytes) -> bool:
    return not data or data.count(0) + data.count(0xFF) == len(data)


def fingerprint(game_id: str, data) -> str | None:
    """Changes exactly when the player saves in-game - never when YellowLink writes the PC boxes there.
    Yellow: SRAM bank 1 sGameData up to sCurBoxData (player, main data incl. play time, party; pret pokeyellow
    sram.asm). Crystal: bank 1 sGameData..sGameDataEnd + sChecksum (pokecrystal sram.asm; sBox is outside).
    Platinum: the whole battery (YellowLink only writes the boxes in RAM). None for other games."""
    if not data:
        return None
    data = bytes(data)
    if game_id == "yellow":
        part = data[GB_BANK + 0x598:GB_BANK + 0x10C0]
    elif game_id == "crystal":
        part = data[GB_BANK + 0x009:GB_BANK + 0xB83] + data[GB_BANK + 0xD0D:GB_BANK + 0xD0F]
    elif game_id == "platinum":
        part = data
    else:
        return None
    return hashlib.sha1(part).hexdigest()


def has_save(game_id: str, data) -> bool:
    """The battery holds an in-game save (something to go back to)."""
    if not data:
        return False
    data = bytes(data)
    if game_id == "yellow":         # sPlayerName: written by the first save (YellowLink's PC writes don't touch it)
        return len(data) > GB_BANK + 0x598 and data[GB_BANK + 0x598] not in (0x00, 0xFF, 0x50)
    if game_id == "crystal":
        from .crystal import checksum16
        region = data[GB_BANK + 0x009:GB_BANK + 0xB83]
        if len(data) < GB_BANK + 0xD0F or _blank(region):
            return False
        return checksum16(region) == data[GB_BANK + 0xD0D] | (data[GB_BANK + 0xD0E] << 8)
    return not _blank(data)


def blocker_text(led: dict) -> str | None:
    """Why the PC/trades make going back unsafe (pcdb ledger since the last in-game save), or None."""
    def first(k):
        v = led.get(k) or []
        if not v:
            return None
        x = v[-1]
        return (x[1] if isinstance(x, list) else x) or "a Pokémon"
    name = first("out")
    if name:
        return f"You put {name.title()} in your PC after your last save"
    name = first("back")
    if name:
        return f"You took {name.title()} out of your PC after your last save"
    name = first("changed")
    if name:
        return f"You changed {name.title()} in your PC after your last save"
    name = first("traded")
    if name:
        return f"You traded/gave away {name.title()} after your last save"
    return None


class ReloadMixin:
    """App part: the button, its confirmation and the after-a-load fix-ups."""

    def init_reload(self, resumed: bool):
        """After the emulator started (resumed = from our snapshot)."""
        self.reload_save = False             # set -> the main loop restarts this game from its in-game save
        self._fp_seen = None
        self._fp_stable = 0
        self._reload_shown = 0.0
        self._ingame_prev = self._in_game_now()
        self._left_game = not self._ingame_prev
        dev = bool(os.environ.get("YL_DEV_LOAD_STATE"))
        # started from the battery (no resume): the game will come back from its save (fast continue or CONTINUE)
        self._battery_due = self._pc_tracked() and not resumed and not dev
        self._battery_note()
        hook = CONTINUE_HOOKS.get(self.game_id)
        if hook and self.full and not self.ds and self._pc_tracked():
            try:
                self.pyboy.hook_register(*hook, self._on_continue_hook, None)
            except Exception:  # noqa: BLE001 - only the after-load fix-ups depend on it
                log.exception("could not hook CONTINUE")

    def _on_continue_hook(self, _ctx=None):
        log.info("%s: CONTINUE - the game loads its save", self.game_id)
        self._battery_due = True

    # helpers ---------------------------------------------------------------------------------------------------
    def _pc_tracked(self) -> bool:
        return bool(self.pc_codec) and not self.chal

    def _in_game_now(self) -> bool:
        try:
            return bool(self.game.in_game())
        except Exception:  # noqa: BLE001
            return False

    def _save_fp(self, data=None):
        if data is None:
            data = self._read_sram()
        return fingerprint(self.game_id, data)

    def _battery_note(self, data=None):
        """Notice an in-game save (the game's since-the-save notes start over)."""
        if not self._pc_tracked():
            return
        try:
            if self.pcsync.note_battery(self.game_id, self._save_fp(data)):
                log.info("in-game save noticed (%s)", self.game_id)
        except Exception:  # noqa: BLE001 - never break the game over this
            log.exception("checking the in-game save failed")

    # the button ------------------------------------------------------------------------------------------------
    def reload_available(self) -> bool:
        """Shown at all? Not in a Soul Link (runs stay honest; the server's ledger enforces deaths anyway)."""
        return not self.chal and not getattr(self, "enter_challenge", None)

    def reload_blocker(self, full=True) -> str | None:
        """Why the button can't be used right now (None = it can). full=False skips the battery read (drawing)."""
        if not self.reload_available():
            return "Not inside a Soul Link."
        if self.trade or self.mode == "trade":
            return "Finish your trade first."
        if self.battle or self.live or self.battle_offer:
            return "Finish your battle with your friend first."
        if getattr(self, "raid_busy", lambda: False)():
            return "Finish your raid first."
        if self._exp_award_busy():
            return "Wait until the experience is handed out."
        if self.pcsync.session_open or getattr(self, "_pt_pc", None):
            return "Close the PC first."
        if getattr(self, "_pt_party_fix", None):
            return "One moment - YellowLink is still tidying your party."
        if not full:
            return None
        data = self._read_sram()
        if not has_save(self.game_id, data):
            return "You haven't saved in-game yet (Start > Save)."
        if self._pc_tracked():
            self._battery_note(data)
            why = blocker_text(self.pcsync.since_save(self.game_id))
            if why:
                return why + ". Save in-game first (Start > Save), then try again."
        return None

    def ask_reload(self):
        """Settings button / F7: open the confirmation (or say why not)."""
        if not self.reload_available():
            return
        why = self.reload_blocker()
        if why:
            self.toast(why, 7, ui.BAD)
            return
        self.close_panel()
        self.release_all()
        self.mode = "reload_confirm"
        self._reload_shown = time.time()
        self._fp_seen = self._save_fp()
        self._fp_stable = self.frame

    def confirm_reload(self, yes: bool):
        if self.mode != "reload_confirm":
            return
        self.mode = "play"
        if not yes:
            return
        why = self.reload_blocker()
        if why is None and self.frame - self._fp_stable < STABLE_FRAMES:
            why = "Your game is saving right now - try again in a moment."
        if why:
            self.toast(why, 7, ui.BAD)
            return
        log.info("back to the last in-game save (%s): restarting from the battery", self.game_id)
        self.cfg["_travel_msg"] = "Going back to your last save..."
        self.reload_save = True
        self.quit_requested = True

    def reload_key(self, k):
        if time.time() - self._reload_shown < 0.4:      # a key still held from opening it
            return
        if k in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_y):
            self.confirm_reload(True)
        elif k in (pygame.K_ESCAPE, pygame.K_n):
            self.confirm_reload(False)

    def draw_reload_confirm(self, sub):
        f = self.fonts
        width = min(self.game_w - 40, 520) - 36
        lines = []
        for s, color in (("Everything since your last in-game save is lost.", ui.TEXT),
                         ("Your YellowLink PC and trades stay as they are.", ui.DIM)):
            lines += [(x, color) for x in ui.wrap(f.body, s, width)]
        lines += ["", ("Continue?", ui.ACCENT), "", ""]
        title = "Back to my last save?" if f.title.size("Back to my last in-game save?")[0] > width else \
            "Back to my last in-game save?"
        r = ui.modal(sub, f, self.game_w, self.game_h, title, lines, "[Enter] yes    [Esc] no")
        from . import panels
        # clicks elsewhere don't reach the game (DS touch screen) while this is open
        self.click_targets.append((pygame.Rect(0, self.game_y, self.game_w, self.game_h), lambda: None, "modal"))
        bw = min(150, (r.w - 54) // 2)
        panels.button(sub, self, f, pygame.Rect(r.right - 18 - bw, r.bottom - 74, bw, 36), "Go back",
                      lambda: self.confirm_reload(True), offset=(0, self.game_y), primary=True)
        panels.button(sub, self, f, pygame.Rect(r.right - 28 - 2 * bw, r.bottom - 74, bw, 36), "Cancel",
                      lambda: self.confirm_reload(False), offset=(0, self.game_y))

    def discard_resume(self):
        """On the way out for the button: the resume snapshot (just written by shutdown = where the player was)
        goes to saves/backups/ (the last 5 are kept, in case of a misclick), so the next start loads the
        in-game save. The flag makes that start go straight into CONTINUE."""
        path = self.cfg.resume_path
        try:
            if path.exists():
                dest = self.cfg.backup_dir / time.strftime(f"{self.save_tag}-resume-%Y%m%d-%H%M%S.state")
                os.replace(path, dest)
                log.info("resume snapshot kept as %s", dest)
                old = sorted(self.cfg.backup_dir.glob(f"{self.save_tag}-resume-*.state"))
                for p in old[:-RESUME_BACKUPS]:
                    try:
                        p.unlink()
                    except OSError:
                        pass
        except OSError:
            log.exception("could not move the resume snapshot away; the game resumes from it instead")
            return
        self.cfg["_reload_boot"] = self.game_id
        self.cfg.save()

    # every frame -----------------------------------------------------------------------------------------------
    def reload_step(self):
        if self.mode == "reload_confirm" and self.frame % FP_CHECK_EVERY == 0:
            fp = self._save_fp()
            if fp != self._fp_seen:
                self._fp_seen, self._fp_stable = fp, self.frame
        if not self._pc_tracked() or self.frame % FP_CHECK_EVERY:
            return
        now = self._in_game_now()
        if not self.ds:
            pass                                    # Game Boy: the CONTINUE hook says when (see CONTINUE_HOOKS)
        elif self._ingame_prev and not now:
            self._left_game = True                  # soft reset / back at the title screen
        elif now and not self._ingame_prev and self._left_game:
            log.info("%s loaded its save again (soft reset + CONTINUE?)", self.game_id)
            self._battery_due = True
        self._ingame_prev = now
        if self._battery_due and now and not self.pcsync.session_open:
            try:
                safe = self.game.is_safe()
            except Exception:  # noqa: BLE001
                safe = False
            if safe:
                self.after_battery_load()

    def after_battery_load(self):
        """The game state is its in-game save again: line the PC's view up with it and take party copies of mons
        put into the PC since that save out again (SPEC.md section 5). Called at a safe frame, or from the PC open
        hook if the player goes straight to the PC."""
        codec = self.pc_codec
        try:
            if self.ds:
                if not codec.initialised():
                    return
                party = self._pt_party_now()
                if party is None:
                    return                          # mid-update: next time
            else:
                party = self.G.party_records(self.game)
            self._battery_due = False
            self._left_game = False
            fp = self._save_fp()
            if self.pcsync.rebase(self.game_id, codec):
                self.pc_trust = True
            clones = self.pcsync.party_clones(self.game_id, codec, party)
            self._remove_party_clones(clones, party)
            self.pcsync.loaded_from_save(self.game_id, fp)
        except Exception:  # noqa: BLE001
            log.exception("lining up the PC with the loaded save failed")
            self._battery_due = False
        if self.cfg.pop("_reload_boot", None) is not None:
            self.cfg.save()
            self.toast("Back at your last in-game save.", 5, ui.GOOD)

    def _remove_party_clones(self, slots, party):
        if not slots:
            return
        names = []
        if self.ds:
            fix = {"remove": [(party[i], [], []) for i in slots], "give": [], "take": [], "until": self.frame + 600}
            old = self._pt_party_fix
            if old:
                for k in ("remove", "give", "take"):
                    fix[k] = old[k] + fix[k]
            self._pt_party_fix = fix
            names = [f"party slot {i + 1}" for i in slots]
        else:
            mons = self.game.party()
            for i in sorted(slots, reverse=True):
                if len(self.game.party()) <= 1:
                    log.warning("party clone in slot %d is the last party Pokémon: left in the party", i)
                    continue
                names.append(mons[i].nick_str() if i < len(mons) else "?")
                self.game.remove_party_slot(i)
        log.warning("took %d Pokémon put into the PC after the last save back out of the party: %s", len(slots),
                    names)
        self.toast("A Pokémon you put in your PC after your last save stays in the PC (no copies) - it was taken "
                   "out of your party.", 8, ui.ACCENT)
