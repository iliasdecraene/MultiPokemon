"""Shiny alerts (SPEC.md section 10): detection on battle_info readings / fake memory, the once-per-battle watch,
the message, the banner queue, the jingle, the app flow (send, banner, jingle, echo) and the Yellow caught ball.
Real-ROM checks: dev/shiny/verify.py; two clients + server: dev/e2e/shiny_alert.py."""
import os
import sys

import numpy as np
import pygame
import pytest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from yellowlink import audio, badge_art, game, shiny, text, ui  # noqa: E402
from test_badges import PLAIN, SHINY, crystal_world, g1_rec, yellow_world  # noqa: E402
from yellowlink import crystal  # noqa: E402


def y_info(dex=16, shiny_=False, kind="wild"):
    return {"kind": kind, "dex": dex, "shiny": shiny_, "safari": False}


def pt_key(pid, tid=0x1234, sid=0x5678):
    return "%08x%04x%04x" % (pid, tid, sid)


def shiny_pid(tid=0x1234, sid=0x5678):
    hi = 0xABCD
    return (hi << 16) | (tid ^ sid ^ hi)


# --- what counts --------------------------------------------------------------------------------------------
def test_wild_shinies_yellow_crystal():
    assert shiny.wild_shinies(y_info(shiny_=True)) == [16]
    assert shiny.wild_shinies(y_info()) == []
    assert shiny.wild_shinies(y_info(shiny_=True, kind="trainer")) == []
    assert shiny.wild_shinies(y_info(shiny_=True, kind="special")) == []
    assert shiny.wild_shinies(None) == []
    assert shiny.wild_shinies(y_info(dex=0, shiny_=True)) == []          # not loaded yet


def test_wild_shinies_platinum_any_wild_mon():
    plain = pt_key(0x11112222)
    shy = pt_key(shiny_pid())
    info = {"kind": "wild", "dex": 396, "shiny": False, "keys": [plain, shy], "dexes": [396, 399]}
    assert shiny.wild_shinies(info) == [399]                            # the second mon of a double battle
    info = {"kind": "wild", "dex": 396, "shiny": True, "keys": [shy], "dexes": [396]}
    assert shiny.wild_shinies(info) == [396]
    assert shiny.wild_shinies({"kind": "wild", "dex": 396, "shiny": False, "keys": [plain], "dexes": [396]}) == []
    assert shiny.wild_shinies({"kind": "trainer", "dex": 396, "shiny": True, "keys": [shy], "dexes": [396]}) == []
    assert shiny.gen4_key_shiny(shy) and not shiny.gen4_key_shiny(plain)
    assert not shiny.gen4_key_shiny("zz") and not shiny.gen4_key_shiny(None)


# --- once per battle --------------------------------------------------------------------------------------------
def feed_all(watch, seq):
    return [watch.feed(i) for i in seq]


def test_watch_settles_once_when_stable():
    w = shiny.EncounterWatch()
    a = y_info(shiny_=True)
    out = feed_all(w, [None, a, a, a, a])
    assert out[:2] == [None, None] and out[2] == a and out[3:] == [None, None]
    assert w.in_battle


def test_watch_waits_for_the_previous_battles_dvs_to_be_replaced():
    # Yellow: on wIsInBattle's first frame the DVs can still be the previous battle's (shiny there, plain now)
    w = shiny.EncounterWatch()
    out = feed_all(w, [y_info(shiny_=True), y_info(), y_info()])
    assert out == [None, None, y_info()]


def test_watch_new_battle_only_after_several_empty_reads():
    w = shiny.EncounterWatch()
    a = y_info(shiny_=True)
    assert feed_all(w, [a, a])[-1] == a
    # one unreadable frame mid-battle (Platinum) must not start a "new" battle
    assert feed_all(w, [None, a, a, a]) == [None] * 4
    assert feed_all(w, [None] * shiny.END_READS) == [None] * shiny.END_READS
    assert not w.in_battle
    assert feed_all(w, [a, a])[-1] == a                                # the next battle settles again


def test_watch_crystal_trainer_battle_settles_as_trainer():
    w = shiny.EncounterWatch()
    t0 = {"kind": "trainer", "dex": 0, "shiny": False}
    t1 = {"kind": "trainer", "dex": 19, "shiny": True}
    out = feed_all(w, [t0, t1, t1, t1])
    assert out[2] == t1 and shiny.wild_shinies(out[2]) == []


# --- real adapters on fake memory ---------------------------------------------------------------------------
def test_yellow_detection_on_fake_memory():
    m, gm = yellow_world([g1_rec(dvs=PLAIN)])
    w = shiny.EncounterWatch()
    assert w.feed(gm.battle_info()) is None
    m[game.IS_IN_BATTLE], m[game.ENEMY_MON_SPECIES2] = 1, 0x24              # wild PIDGEY (internal 0x24)
    gm.write(game.ENEMY_MON_DVS, SHINY)
    w.feed(gm.battle_info())
    got = w.feed(gm.battle_info())
    assert got is not None and shiny.wild_shinies(got) == [gm.rom.dex_no(0x24)]
    m[game.BATTLE_TYPE] = game.BATTLE_TYPE_OLD_MAN                          # not catchable: never
    assert shiny.wild_shinies(gm.battle_info()) == []


def test_crystal_detection_on_fake_memory():
    _, cm = crystal_world([])
    cm.w(crystal.BATTLE_MODE, 1)
    cm.w(crystal.ENEMY_MON_SPECIES, 129)
    cm.w(crystal.ENEMY_STATS + 9, 20)                                      # enemy loaded
    cm.write(crystal.ENEMY_MON_DVS, SHINY)
    info = cm.battle_info()
    assert info["kind"] == "wild" and shiny.wild_shinies(info) == [129]
    cm.write(crystal.ENEMY_MON_DVS, PLAIN)
    assert shiny.wild_shinies(cm.battle_info()) == []


# --- message ---------------------------------------------------------------------------------------------------
def test_found_message_shape():
    msg = shiny.found_message("Pidgey", 16, "Route 1", "yellow")
    assert msg == {"type": "shiny_found", "species": "Pidgey", "dex": 16, "area": "Route 1", "game": "yellow"}
    long = shiny.found_message("Abcdefghijklmnop", 1, "x" * 80, "crystal")
    assert len(long["species"]) <= 12 and len(long["area"]) <= 40


def test_species_display():
    assert shiny.species_label("NIDORAN♀") == "Nidoran F"           # no ♀/♂ glyph in our font
    assert shiny.species_label("NIDORAN♂") == "Nidoran M"
    assert shiny.species_label("Nidoran♀") == "Nidoran F"           # Platinum names from the ROM


@pytest.mark.parametrize("area, phrase", [("Route 1", "on Route 1"), ("Route 202", "on Route 202"),
                                          ("Viridian Forest", "in Viridian Forest"), ("Mt. Moon", "in Mt. Moon"),
                                          ("Eterna City", "in Eterna City"), ("Union Cave", "in Union Cave")])
def test_area_phrase(area, phrase):
    assert shiny.area_phrase(area) == phrase
    assert shiny.species_label("MR.MIME") == "Mr.Mime"
    assert shiny.species_label("#25") == "#25"


# --- banners ---------------------------------------------------------------------------------------------------
def test_banner_queue_and_timing():
    b = shiny.Banners()
    assert b.update(0.0) is None and b.current(0.0) is None
    b.push("A shiny Pidgey appeared!", "Route 1", now=10.0)
    started = b.update(10.0)
    assert started and started.title == "A shiny Pidgey appeared!"
    assert b.update(10.1) is None                                          # already running: no second start
    b.push("Alex found a shiny Onix!", "on Union Cave", now=11.0)
    assert b.current(12.0).title.startswith("A shiny")
    assert b.update(10.0 + shiny.BANNER_SECS + 0.01).title.startswith("Alex")   # the next one starts
    assert b.current(10.0 + 2 * shiny.BANNER_SECS + 0.1) is None
    for i in range(10):
        b.push(f"t{i}", "", now=100.0)
    assert len(b.queue) <= shiny.MAX_QUEUED


def test_banner_alpha_fades():
    b = shiny.Banners()
    b.push("x", "", now=0.0)
    b.update(0.0)
    cur = b.current(0.0)
    assert 0 <= cur.alpha(0.0) < 0.5 and cur.alpha(1.0) == 1.0 and cur.alpha(shiny.BANNER_SECS - 0.05) < 0.5


# --- jingle ----------------------------------------------------------------------------------------------------
def test_jingle_samples():
    a = shiny.jingle_samples(48000, 2)
    assert a.dtype == np.int16 and a.ndim == 2 and a.shape[1] == 2
    secs = len(a) / 48000
    assert 1.0 <= secs <= 2.0
    peak = int(np.abs(a.astype(np.int32)).max())
    assert 0.25 * 32767 * shiny.JINGLE_VOLUME <= peak <= 32767 * shiny.JINGLE_VOLUME + 1
    assert np.abs(a[:20]).max() < 2000 and np.abs(a[-200:]).max() < 300     # no click at either end
    mono = shiny.jingle_samples(22050, 1)
    assert mono.shape[1] == 1 and abs(len(mono) / 22050 - secs) < 0.01


def test_jingle_sound_and_fixed_volume_channel():
    pygame.mixer.quit()
    out = audio.AudioOut(volume=0)                          # the game's slider at 0 and muted ...
    out.set_muted(True)
    assert out.ok
    snd = shiny.jingle_sound()
    assert snd is not None and 1.0 <= snd.get_length() <= 2.0
    assert out.play_fx(snd)                                 # ... the jingle still plays, on its own channel
    assert out.fx_channel is not None and out.fx_channel is not out.channel
    assert pygame.mixer.get_num_channels() > audio.FX_CHANNEL
    assert out.fx_channel.get_volume() == 1.0
    out.close()


def test_no_sound_card_never_crashes(monkeypatch):
    out = audio.AudioOut.__new__(audio.AudioOut)
    out.ok, out.channel, out.fx_channel = False, None, None
    assert out.play_fx(object()) is False
    monkeypatch.setattr(pygame.mixer, "get_init", lambda: None)
    assert shiny.jingle_sound() is None


# --- the app side -----------------------------------------------------------------------------------------------
class FakeNet:
    status = "online"

    def __init__(self):
        self.sent = []

    def send(self, msg):
        self.sent.append(msg)
        return True


class FakeG:
    @staticmethod
    def area_of(map_id):
        return "ROUTE_1"

    @staticmethod
    def area_name(key):
        return "Route 1"


class FakeGame:
    def __init__(self):
        self.info = None

    def battle_info(self):
        return self.info

    def map_id(self):
        return 12


class ShinyApp(shiny.ShinyMixin):
    def __init__(self, game_id="yellow", features=("shiny_alert",)):
        self.cfg = {"uid": "me", "name": "Ilias"}
        self.net = FakeNet()
        self.game, self.G, self.game_id = FakeGame(), FakeG, game_id
        self.full, self.chal, self.live, self.battle, self.ds = True, None, None, None, game_id == "platinum"
        self.server_features = set(features)
        self.frame = 0
        self.log = []
        self.jingles = 0
        self.init_shiny()

    def dex_name(self, dex):
        return {16: "Pidgey", 129: "Magikarp", 399: "Bidoof"}.get(dex, f"#{dex}")

    def sys_msg(self, s, *a):
        self.log.append(s)

    def play_shiny_jingle(self):
        self.jingles += 1

    def run(self, frames):
        for _ in range(frames):
            self.shiny_step()
            self.frame += 1


def test_finder_sends_once_and_gets_banner_and_jingle():
    app = ShinyApp()
    app.game.info = y_info(shiny_=True)
    app.run(shiny.POLL_EVERY * 6)
    assert app.net.sent == [shiny.found_message("Pidgey", 16, "Route 1", "yellow")]
    assert app.banners.current_title() == "A shiny Pidgey appeared!"
    assert app.jingles == 1
    app.game.info = None
    app.run(shiny.POLL_EVERY * (shiny.END_READS + 1))
    app.game.info = y_info(shiny_=False)
    app.run(shiny.POLL_EVERY * 6)
    assert len(app.net.sent) == 1 and app.jingles == 1                    # next battle: plain -> nothing


def test_old_server_no_message_but_local_banner_and_line():
    app = ShinyApp(features=("badges",))
    app.game.info = y_info(shiny_=True)
    app.run(shiny.POLL_EVERY * 6)
    assert app.net.sent == []
    assert app.jingles == 1 and app.banners.current_title() == "A shiny Pidgey appeared!"
    assert app.log and app.log[-1].startswith("✨ You found a shiny Pidgey on Route 1")
    assert app.banners.current().sub == "on Route 1"


def test_no_alert_outside_full_support_or_in_link_battles():
    app = ShinyApp()
    app.full = False
    app.game.info = y_info(shiny_=True)
    app.run(shiny.POLL_EVERY * 6)
    assert app.net.sent == [] and app.jingles == 0
    app = ShinyApp()
    app.live = object()                                                   # a live PvP battle
    app.game.info = y_info(shiny_=True)
    app.run(shiny.POLL_EVERY * 6)
    assert app.net.sent == [] and app.jingles == 0


def test_platinum_double_battle_alert_names_the_shiny_one():
    app = ShinyApp(game_id="platinum")
    app.game.info = {"kind": "wild", "dex": 396, "shiny": False, "keys": [pt_key(1), pt_key(shiny_pid())],
                     "dexes": [396, 399]}
    app.run(shiny.POLL_EVERY * 6)
    assert app.net.sent[-1]["species"] == "Bidoof" and app.net.sent[-1]["game"] == "platinum"


def test_a_bad_read_never_breaks_the_loop():
    app = ShinyApp()

    def boom():
        raise RuntimeError("bus error")
    app.game.battle_info = boom
    app.run(shiny.POLL_EVERY * 4)
    assert app.net.sent == []


def sysmsg(uid="alex", name="Alex", species="Onix", area="Union Cave", soullink=False):
    return {"type": "system", "text": f"✨ {name} found a shiny {species} {shiny.area_phrase(area)}!",
            "shiny": {"uid": uid, "name": name, "species": species, "dex": 95, "area": area, "game": "crystal",
                      "soullink": soullink}}


def test_others_get_banner_and_jingle_finder_echo_is_quiet():
    app = ShinyApp()
    app.shiny_on_system(sysmsg())
    app.run(1)
    assert app.banners.current_title() == "Alex found a shiny Onix!" and app.jingles == 1
    assert app.banners.current().sub == "in Union Cave"
    app2 = ShinyApp()
    app2.shiny_on_system(sysmsg(uid="me", name="Ilias"))                    # our own, echoed by the server
    app2.run(1)
    assert app2.banners.current() is None and app2.jingles == 0
    app3 = ShinyApp()
    app3.shiny_on_system(sysmsg(soullink=True))
    app3.run(1)
    assert app3.banners.current().sub == "in Union Cave - Soul Link"
    app4 = ShinyApp()
    app4.shiny_on_system(sysmsg(species="Nidoran♂", area="Route 22"))      # an older server passes ♂ through
    app4.run(1)
    assert app4.banners.current_title() == "Alex found a shiny Nidoran M!"
    assert app4.banners.current().sub == "on Route 22"


def test_malformed_shiny_field_is_ignored():
    app = ShinyApp()
    for bad in ({"shiny": "yes"}, {"shiny": {"uid": 5}}, {"shiny": {"uid": "a", "name": None, "species": "x"}}, {}):
        app.shiny_on_system(dict(type="system", text="hi", **bad))
    app.run(1)
    assert app.banners.current() is None and app.jingles == 0


# --- Yellow: caught ball ----------------------------------------------------------------------------------------
def hud(m, gm, nick="PIDGEY", col=1):
    raw = text.encode(nick, 11)
    gm.write(game.ENEMY_MON_NICK, raw)
    name = raw.split(b"\x50")[0]
    for i, b in enumerate(name):
        m[game.TILEMAP + col + i] = b
    m[game.TILEMAP + 3 * 20 + 1] = game.HUD_CORNER_TILE


def test_enemy_hud_shown():
    m, gm = yellow_world([])
    assert not gm.enemy_hud_shown()
    m[game.IS_IN_BATTLE] = 1
    hud(m, gm, "PIDGEY", col=1)
    assert gm.enemy_hud_shown()
    m[game.TILEMAP + 3 * 20 + 1] = 0x7F                                    # HUD frame gone (cleared)
    assert not gm.enemy_hud_shown()
    m2, gm2 = yellow_world([])
    m2[game.IS_IN_BATTLE] = 1
    hud(m2, gm2, "ONIX", col=2)                                            # short names are centred
    assert gm2.enemy_hud_shown()
    m2[game.IS_IN_BATTLE] = 0
    assert not gm2.enemy_hud_shown()


def test_yellow_owned_flag_set_at_battle_start():
    m, gm = yellow_world([])
    app = ShinyApp()
    app.game = gm
    app.G = game
    dex = gm.rom.dex_no(0x24)
    m[game.DEX_OWNED + (dex - 1) // 8] |= 1 << ((dex - 1) % 8)
    m[game.IS_IN_BATTLE], m[game.ENEMY_MON_SPECIES2] = 1, 0x24
    gm.write(game.ENEMY_MON_DVS, PLAIN)
    app.run(shiny.POLL_EVERY * 4)
    assert app.wild_owned
    m[game.IS_IN_BATTLE] = 0
    app.run(shiny.POLL_EVERY * (shiny.END_READS + 1))
    assert not app.wild_owned
    m[game.DEX_OWNED + (dex - 1) // 8] = 0                                 # not caught yet
    m[game.IS_IN_BATTLE] = 1
    app.run(shiny.POLL_EVERY * 4)
    assert not app.wild_owned
    m[game.DEX_OWNED + (dex - 1) // 8] |= 1 << ((dex - 1) % 8)              # caught during this battle: stays off
    app.run(shiny.POLL_EVERY * 4)
    assert not app.wild_owned
    m[game.IS_IN_BATTLE] = 2                                               # trainer battle: never
    app.run(shiny.POLL_EVERY * (shiny.END_READS + 4))
    assert not app.wild_owned


def test_owned_flag_only_in_yellow():
    app = ShinyApp(game_id="crystal")
    app.game.info = y_info(dex=129)
    app.run(shiny.POLL_EVERY * 4)
    assert not app.wild_owned


# --- drawing ---------------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def fonts():
    pygame.init()
    pygame.display.set_mode((10, 10))
    return ui.Fonts()


@pytest.mark.parametrize("size", [(480, 432), (640, 576), (384, 576), (320, 288)])
def test_banner_fits(fonts, size):
    surf = pygame.Surface(size)
    b = shiny.Banners()
    b.push("Ilias found a shiny Fletchinder!", "on Route 210 - Soul Link", now=0.0)
    b.update(0.0)
    r = badge_art.draw_shiny_banner(surf, fonts, b.current(0.0), 1.0)
    assert surf.get_rect().contains(r) and r.top <= 12


@pytest.mark.parametrize("scale", [2, 3, 4])
def test_owned_ball_stays_in_its_tile(fonts, scale):
    surf = pygame.Surface((160 * scale, 144 * scale))
    r = badge_art.draw_owned_ball(surf, 8 * scale, 8 * scale, scale)
    tile = pygame.Rect(8 * scale, 8 * scale, 8 * scale, 8 * scale)
    assert tile.contains(r)


def test_chat_sparkle_line(fonts):
    segs = ui.chat_segments("✨ Alex found a shiny Onix on Union Cave!")
    assert segs[0] == ui.SPARKLE and "✨" not in "".join(s for s in segs[1:] if isinstance(s, str))
    assert ui.chat_segments("hello") == ["hello"]
