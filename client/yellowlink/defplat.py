"""Definitive Platinum: make it from the player's own Platinum with the patch from their server (SPEC.md section 10).

"Pokémon Definitive Platinum" is BigNation's hack (https://www.pokecommunity.com/threads/pok%C3%A9mon-definitive-platinum-complete.539178/).
YellowLink never ships it or the patch: the host puts BigNation's xdelta patches on their server; this mixin downloads
the one that fits the player's Platinum (rev 0 or rev 1) in 1 MiB ranges (resumable), applies it in a background
thread (vcdiff.py), keeps the result only if its SHA1 is Definitive Platinum's and stores it in the ROM library next to
the player's other game files. The player's original file is never written.

Mixed into App (app.py). The Regions panel (panels.py) draws the state; prompts use App.prompt (kind "defplat").
"""
from __future__ import annotations

import base64
import hashlib
import logging
import threading
import time
from pathlib import Path

from . import library, ui, vcdiff
from .config import app_dir

log = logging.getLogger("yellowlink.defplat")

PATCH_NAME = "definitive_platinum"
RANGE = 1 << 20                  # bytes per patch_get (the server sends them in 48 KiB chunks)
STALL = 20.0                     # no chunk for this long while online: ask again from where we are
TITLE = library.VARIANT_TITLES["definitive"]
CREDIT = "Definitive Platinum is by BigNation (PokéCommunity)."


class _Cancelled(Exception):
    pass


class DefPlatMixin:
    def init_defplat(self):
        self.server_sinnoh = self.lib.server_sinnoh()       # until the server says (remembered per server)
        self.server_patches: dict = {}                      # rev -> {"size", "sha1"} offered by this server
        self.dp: dict | None = None                         # the job: download -> patch -> done | failed
        self._dp_nudged = False
        self._dp_welcomed = False                           # nudges wait for the server's welcome

    # --- what the panels show ---------------------------------------------------------------------------------
    def defplat_offer(self):
        """(rev, source path, {"size", "sha1"}) when this server has the patch for the player's Platinum."""
        src = self.lib.patch_source()
        if not src:
            return None
        info = self.server_patches.get(src[0])
        return (src[0], src[1], info) if info else None

    def defplat_status(self) -> str:
        """"busy" (downloading/patching) | "failed" | "have" | "offer" | "rev1" (rev 1 only, no patch here) | ""."""
        if self.dp and self.dp["phase"] in ("download", "patch"):
            return "busy"
        if self.dp and self.dp["phase"] == "failed":
            return "failed"
        if "definitive" in self.lib.variants("platinum"):
            return "have"
        if self.defplat_offer():
            return "offer"
        if self.lib.patch_source() and self.lib.patch_source()[0] == "rev1":
            return "rev1"
        return ""

    def defplat_progress(self) -> tuple:
        """(fraction 0..1, label) of the running job."""
        dp = self.dp or {}
        if dp.get("phase") == "download":
            frac = dp["got"] / max(1, dp["size"])
            return frac, f"Downloading the patch... {int(frac * 100)}%"
        if dp.get("phase") == "patch":
            frac = dp.get("progress", 0.0)
            return frac, f"Making {TITLE}... {int(frac * 100)}%"
        return 0.0, ""

    def run_variant(self) -> str:
        """The Sinnoh game a new Platinum Soul Link would be played with."""
        if self.sinnoh_required():
            return "definitive"
        ok = self.defplat_variants()
        v = self.variant if self.game_id == "platinum" and not self.chal else self.lib.variant("platinum")
        return v if v in ok else (ok[0] if ok else "vanilla")

    def sinnoh_required(self) -> bool:
        return self.server_sinnoh == "definitive"

    # --- server ------------------------------------------------------------------------------------------------
    def defplat_on_welcome(self, msg):
        feats = set(msg.get("features") or [])
        sinnoh = msg.get("sinnoh") if msg.get("sinnoh") in ("any", "definitive") else "any"
        patches = (msg.get("patches") or {}).get(PATCH_NAME) if "defplat" in feats else None
        self.server_patches = {r: i for r, i in (patches or {}).items()
                               if r in ("rev0", "rev1") and isinstance(i, dict) and isinstance(i.get("sha1"), str)
                               and isinstance(i.get("size"), int) and i["size"] > 0}
        self.server_sinnoh = sinnoh
        entry = self.cfg.server(self.cfg.server_id)
        if entry is not None and entry.get("sinnoh", "any") != sinnoh:
            entry["sinnoh"] = sinnoh                         # the next start picks the right Sinnoh game
            self.cfg.save()
        dp = self.dp
        if dp and dp["phase"] == "download":
            info = self.server_patches.get(dp["rev"])
            if info and info["sha1"] == dp["sha1"]:
                self._dp_request()                           # carry on after a reconnect
            else:
                self._dp_fail("The server's patch file is gone or changed - try again.", keep_resume=False)
        elif self.cfg.get("defplat_resume") and not self.dp:
            if self.defplat_offer():
                log.info("resuming the Definitive Platinum job")
                self.make_definitive(quiet=True)
            else:
                self.cfg.pop("defplat_resume", None)
                self.cfg.save()
        self._dp_nudged = False
        self._dp_welcomed = True

    def defplat_variants(self) -> list:
        """`library.variants.platinum`: the Platinum variants we can play everything in (Soul Link needs the
        adapter verified on that ROM)."""
        return [v for v in self.lib.variants("platinum") if library.variant_full(v)]

    def on_patch_chunk(self, msg):
        dp = self.dp
        if not dp or dp["phase"] != "download" or msg.get("rev") != dp["rev"] or msg.get("sha1") != dp["sha1"]:
            return
        if msg.get("offset") != dp["got"]:
            return                              # a chunk of a range we asked again for: already have it / later
        try:
            data = base64.b64decode(msg.get("data") or "", validate=True)
        except (ValueError, TypeError):
            return
        if not data or dp["got"] + len(data) > dp["size"]:
            self._dp_fail("The download went wrong - try again.", keep_resume=False)
            return
        try:
            with open(dp["part"], "ab") as f:
                f.write(data)
        except OSError as e:
            self._dp_fail(f"Couldn't save the download ({e.strerror or e}).")
            return
        dp["got"] += len(data)
        dp["last"] = time.time()
        if dp["got"] >= dp["size"]:
            self._dp_downloaded()
        elif dp["got"] >= dp["range_end"]:
            self._dp_request()

    def on_patch_error(self, msg):
        dp = self.dp
        if not dp or dp["phase"] != "download" or msg.get("rev") != dp["rev"]:
            return
        if msg.get("reason") == "changed" and isinstance(msg.get("sha1"), str) and isinstance(msg.get("size"), int):
            log.info("patch changed on the server: starting over")
            self.server_patches[dp["rev"]] = {"sha1": msg["sha1"], "size": msg["size"]}
            self.dp = None
            self.make_definitive(quiet=True)
            return
        self._dp_fail("The server doesn't have the Definitive Platinum patch (any more). Ask the host.",
                      keep_resume=False)

    # --- the job -------------------------------------------------------------------------------------------------
    def make_definitive(self, quiet=False):
        """Regions > Make Definitive Platinum (or the prompt): download the patch, then patch the player's ROM."""
        if self.dp and self.dp["phase"] in ("download", "patch"):
            return
        offer = self.defplat_offer()
        if offer is None:
            self.toast("Your server has no Definitive Platinum patch for your Platinum.", 5, ui.BAD)
            return
        rev, src, info = offer
        dl = app_dir() / "downloads"
        dl.mkdir(parents=True, exist_ok=True)
        stem = f"{PATCH_NAME}-{rev}-{info['sha1'][:12]}.xdelta"
        for old in dl.glob(f"{PATCH_NAME}-{rev}-*"):          # an older patch (the host replaced it)
            if not old.name.startswith(stem):
                try:
                    old.unlink()
                except OSError:
                    pass
        part, done = dl / (stem + ".part"), dl / stem
        self.dp = {"phase": "download", "rev": rev, "src": src, "sha1": info["sha1"], "size": info["size"],
                   "part": part, "patch": done, "got": 0, "range_end": 0, "last": time.time(), "progress": 0.0,
                   "retries": 0, "msg": ""}
        self.cfg["defplat_resume"] = rev
        self.cfg.save()
        if not quiet:
            self.toast("Getting Definitive Platinum ready - keep playing! Your Platinum file stays as it is.", 6,
                       ui.ACCENT)
        log.info("making Definitive Platinum from %s (%s), patch %s", src, rev, info["sha1"])
        if done.exists():
            self.dp["got"] = self.dp["size"]
            self._dp_downloaded()
            return
        try:
            got = part.stat().st_size if part.exists() else 0
        except OSError:
            got = 0
        if got > info["size"]:
            part.unlink(missing_ok=True)
            got = 0
        self.dp["got"] = got
        if got == info["size"]:
            self._dp_downloaded()
        else:
            self._dp_request()

    def _dp_request(self):
        dp = self.dp
        if getattr(self.net, "status", "") != "online":
            return                                  # asked again after the next welcome
        dp["range_end"] = min(dp["got"] + RANGE, dp["size"])
        dp["last"] = time.time()
        self.net.send({"type": "patch_get", "name": PATCH_NAME, "rev": dp["rev"], "sha1": dp["sha1"],
                       "offset": dp["got"], "length": dp["range_end"] - dp["got"]})

    def _dp_downloaded(self):
        dp = self.dp
        if dp["part"].exists():
            h = hashlib.sha1()
            with open(dp["part"], "rb") as f:
                for block in iter(lambda: f.read(1 << 20), b""):
                    h.update(block)
            if h.hexdigest() != dp["sha1"]:
                log.warning("downloaded patch has the wrong SHA1 (%s)", h.hexdigest())
                dp["part"].unlink(missing_ok=True)
                dp["retries"] += 1
                if dp["retries"] > 2:
                    self._dp_fail("The download keeps getting damaged - try again later.", keep_resume=False)
                    return
                dp["got"] = 0
                self._dp_request()
                return
            dp["part"].replace(dp["patch"])
        dp["phase"] = "patch"
        dp["progress"] = 0.0
        dp["cancel"] = False
        dp["result"] = None
        dp["thread"] = threading.Thread(target=self._dp_worker, args=(dp,), name="defplat-patch", daemon=True)
        dp["thread"].start()

    def _dp_worker(self, dp):
        def progress(done, total):
            dp["progress"] = done / max(1, total)
            if dp["cancel"]:
                raise _Cancelled()
        dest = self.lib.definitive_dest()
        try:
            t0 = time.time()
            vcdiff.decode_file(dp["src"], dp["patch"], dest, progress=progress, expect_sha1=library.DEFPLAT_SHA1)
            log.info("Definitive Platinum made in %.1f s", time.time() - t0)
            dp["result"] = ("ok", str(dest))
        except _Cancelled:
            dp["result"] = ("cancelled", "")
        except vcdiff.WrongSourceError as e:
            log.warning("patching failed: %s", e)
            dp["result"] = ("wrong", str(e))
        except vcdiff.VCDiffError as e:
            log.warning("patching failed: %s", e)
            dp["result"] = ("damaged", str(e))
        except OSError as e:
            log.exception("patching failed")
            dp["result"] = ("disk", e.strerror or str(e))
        except Exception as e:  # noqa: BLE001 - never kill the game over this
            log.exception("patching failed")
            dp["result"] = ("error", str(e))

    def defplat_step(self):
        dp = self.dp
        if dp and dp["phase"] == "download" and getattr(self.net, "status", "") == "online" \
                and time.time() - dp["last"] > STALL:
            log.info("patch download stalled at %d/%d: asking again", dp["got"], dp["size"])
            self._dp_request()
        if dp and dp["phase"] == "patch" and dp.get("result"):
            dp["thread"].join(timeout=1)
            self._dp_finish(dp["result"])
        if not self._dp_nudged and getattr(self.net, "status", "") == "online" and self.mode == "play" \
                and not self.prompt and self._dp_welcomed:
            self._dp_nudged = True
            self._defplat_nudge()

    def _dp_finish(self, result):
        dp = self.dp
        kind, detail = result
        if kind == "ok":
            self.lib.set_variant_file("platinum", "definitive", detail)
            self.lib.pick_variant("platinum", "definitive")
            try:
                dp["patch"].unlink()
            except OSError:
                pass
            dp["phase"] = "done"
            self.cfg.pop("defplat_resume", None)
            self.cfg.save()
            self.send_library()
            self.sys_msg(f"{TITLE} is ready! {CREDIT}", ui.GOOD)
            if self.game_id == "platinum" and not self.chal and self.variant != "definitive":
                self._defplat_prompt("switch_ready")
            else:
                self.toast(f"{TITLE} is ready - travel to Sinnoh to play it!", 6, ui.GOOD)
        elif kind == "cancelled":
            self.dp = None
        elif kind == "wrong":
            self._dp_fail("This Platinum file can't be made into Definitive Platinum (it isn't an unchanged US "
                          "Platinum). Nothing was changed.", keep_resume=False)
        elif kind == "damaged":
            try:
                dp["patch"].unlink()                 # download it again next time
            except OSError:
                pass
            self._dp_fail("The patch file was damaged - try again.", keep_resume=False)
        else:
            self._dp_fail(f"Couldn't make Definitive Platinum ({detail}).")

    def _dp_fail(self, text, keep_resume=True):
        if self.dp is None:
            self.dp = {"phase": "failed"}
        self.dp["phase"] = "failed"
        self.dp["msg"] = text
        if not keep_resume:
            self.cfg.pop("defplat_resume", None)
            self.cfg.save()
        self.sys_msg(text, ui.BAD)
        self.toast(text, 8, ui.BAD)

    def defplat_shutdown(self):
        dp = self.dp
        if dp and dp["phase"] == "patch" and dp.get("thread"):
            dp["cancel"] = True
            dp["thread"].join(timeout=10)       # the progress callback stops it within a few ms

    # --- switching + prompts -----------------------------------------------------------------------------------
    def switch_sinnoh(self, variant):
        """Regions > Play with: Platinum / Definitive."""
        if variant not in self.lib.variants("platinum") or (self.sinnoh_required() and variant != "definitive"):
            return
        self.lib.pick_variant("platinum", variant)
        if self.game_id == "platinum" and not self.chal and self.variant != variant:
            self._defplat_prompt("switch", variant)

    def _defplat_switch_now(self, variant):
        self.lib.pick_variant("platinum", variant)
        self.close_panel()
        self.travel("platinum")
        self.cfg["_travel_msg"] = f"Starting {library.VARIANT_TITLES[variant]}..."

    def _defplat_prompt(self, what, variant="definitive"):
        if self.prompt and self.prompt.get("kind") != "defplat":
            return                              # a friend's invite is open: never replace it
        self.prompt = {"kind": "defplat", "what": what, "variant": variant, "until": time.time() + 120,
                       "shown": None}
        if self.mode == "panel":
            self.close_panel()
        self._maybe_show_prompt()

    def _defplat_nudge(self):
        """On a server that plays Definitive Platinum: ask (Sinnoh) or tell (elsewhere) once per connection."""
        if not self.sinnoh_required() or self.chal:
            return
        have = self.lib.variants("platinum")
        in_vanilla = self.game_id == "platinum" and self.variant != "definitive"
        if "definitive" in have:
            if in_vanilla:
                self._defplat_prompt("switch_required")
        elif self.dp and self.dp["phase"] in ("download", "patch"):
            return
        elif self.defplat_offer():
            if in_vanilla:
                self._defplat_prompt("make")
            else:
                self.sys_msg("This server plays Definitive Platinum in Sinnoh - open Regions to make it from "
                             "your Platinum.", ui.ACCENT)
        elif self.lib.patch_source():
            self.sys_msg("This server plays Definitive Platinum, but it has no patch for your Platinum yet - "
                         "ask the host.", ui.ACCENT)

    def defplat_prompt_lines(self, p) -> tuple:
        """(title, lines, yes label, no label) of a "defplat" prompt."""
        what = p.get("what")
        save = ("Your game restarts from your last in-game save: save first (Start > Save).", ui.ACCENT)
        if what == "make":
            return (TITLE, ["This server plays Definitive Platinum (BigNation's", "improved Platinum).",
                            ("Make it from your Platinum now? It takes a minute;", ui.DIM),
                            ("your Platinum file stays as it is.", ui.DIM)], "Make it", "Later")
        if what == "switch_required":
            return (TITLE, ["This server plays Definitive Platinum.", "Switch to it now?", save], "Switch",
                    "Later")
        if what == "switch_ready":
            return (TITLE, [f"{TITLE} is ready!", "Play it now?", save], "Play it", "Later")
        title = library.VARIANT_TITLES.get(p.get("variant"), "Platinum")
        return (f"Switch to {title}?", [f"Sinnoh will be played with {title}.", save], "Switch", "Not now")

    def answer_defplat(self, accept: bool):
        p = self.prompt
        if not p or p.get("kind") != "defplat":
            return
        self.prompt = None
        if self.mode == "prompt":
            self.mode = "play"
        if not accept:
            return
        if p.get("what") == "make":
            self.make_definitive()
        else:
            self._defplat_switch_now(p.get("variant") or "definitive")

    def defplat_backup(self):
        """Before the first start of Definitive with an existing save: one copy of the Sinnoh save per server
        folder, never pruned (saves/backups/before-definitive-platinum-<time>.sav)."""
        save = self.cfg.save_path
        bdir = self.cfg.backup_dir
        if not save.exists() or any(bdir.glob("before-definitive-platinum-*.sav")):
            return None
        dest = bdir / time.strftime("before-definitive-platinum-%Y%m%d-%H%M%S.sav")
        try:
            dest.write_bytes(save.read_bytes())
            log.info("Sinnoh save backed up before the first Definitive Platinum start: %s", dest)
            return dest
        except OSError:
            log.exception("could not back up the Sinnoh save")
            return None


def resume_rom_path(resume: Path) -> Path:
    """The file next to a resume snapshot that says which ROM it was taken on (SHA1)."""
    return resume.with_name(resume.name + ".rom")
