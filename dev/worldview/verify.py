# --- dev harness paths (set YL_ROM / YL_STATES, see dev/README.md) ---
import os as _os, sys as _sys
_REPO = _os.path.abspath(_os.path.join(_os.path.dirname(__file__), "..", ".."))
ROM = _os.environ.get("YL_ROM", _os.path.join(_REPO, "dev", "states", "yellow.gb"))
STATES = _os.environ.get("YL_STATES", _os.path.join(_REPO, "dev", "states"))
_sys.path.insert(0, _os.path.join(_REPO, "client"))
# ---------------------------------------------------------------------
"""Verify yellowlink.worldview against the real emulator (needs the ROM).

Walks the player from the bedroom save state downstairs, out into Pallet
Town and around (map edges with and without connections, Oak's cut-scene),
rendering a wide view every frame and checking:

 1. centre match: our map render for the area under the real 160x144 frame
    vs pyboy.screen.ndarray, ignoring pixels covered by OAM sprites.
 2. margin consistency: the real screen at frame t vs the *margin* part of
    wide renders made 20..90 frames earlier (same map, or translated across a
    map connection).  This checks the extended area really shows what later
    scrolls into view.

Usage: python3 verify.py [--scroll]
  --scroll  pass pyboy.screen.tilemap_position_list[0][:2] as `scroll`
            (default: rely on render() being called every frame)
"""
import sys
import time

sys.path.insert(0, _os.path.join(_REPO, "client"))
sys.path.insert(0, ".")

import numpy as np
from PIL import Image
from pyboy import PyBoy

from nav import Walker
from yellowlink.worldview import WorldView, SCREEN_W, SCREEN_H

ROM = ROM
STATE = _os.path.join(STATES, "room.state")
OUT = "."

pb = PyBoy(ROM, window="null", sound_emulated=False)
pb.set_emulation_speed(0)
pb.load_state(open(STATE, "rb"))
m = pb.memory
wv = WorldView(open(ROM, "rb").read())
MX, MY = wv.mx, wv.my

stats = {}          # map -> [frames, rendered, pixel_ok, pixel_total]
none_reasons = {}
low_frames = []
history = []        # (frame, map, origin, world_img, conn_deltas)
margin = {}         # map -> [ok, total]
frame_no = [0]
npc_stats = [0, 0, 0]
walk_stats = [0, 0, 0]
npc_bad = []
oam_prev = set()
USE_SCROLL = "--scroll" in sys.argv
DUMP_FRAMES = {int(a) for a in sys.argv[1:] if a.isdigit()}
render_time = []
saved = []
want_png = {}       # label -> predicate run per frame


def conn_deltas():
    """map id -> (dx, dy) pixel shift from this map's coords to that map's."""
    w, h = m[0xD368], m[0xD367]
    res = {}
    for d in range(4):
        base = 0xD370 + 11 * d
        cid = m[base]
        if cid == 0xFF:
            continue
        hdr = wv.map_header(cid)
        if hdr is None:
            continue
        _, ch, cw, _ = hdr
        yal, xal = m[base + 7], m[base + 8]
        s8 = lambda v: v - 256 if v >= 128 else v
        if d in (0, 1):
            o = -s8(xal) // 2
            res[cid] = (-o * 32, ch * 32 if d == 0 else -h * 32)
        else:
            o = -s8(yal) // 2
            res[cid] = (cw * 32 if d == 2 else -w * 32, -o * 32)
    return res


def sprite_mask():
    mask = np.zeros((SCREEN_H, SCREEN_W), bool)
    for i in range(40):
        y, x = m[0xFE00 + 4 * i] - 16, m[0xFE00 + 4 * i + 1] - 8
        if -8 < y < SCREEN_H and -8 < x < SCREEN_W:
            mask[max(y, 0):y + 8, max(x, 0):x + 8] = True
    return mask


def on_frame():
    _on_frame()
    # the next frame on screen is drawn with the OAM as it is now
    o = m[0xFE00:0xFEA0]
    oam_prev.clear()
    oam_prev.update((o[4 * i] - 16, o[4 * i + 1] - 8) for i in range(40))


def _on_frame():
    frame_no[0] += 1
    f = frame_no[0]
    if f in DUMP_FRAMES:
        with open(f"{OUT}/dbg_{f}.state", "wb") as fh:
            pb.save_state(fh)
    cur = m[0xD35D]
    s = stats.setdefault(cur, [0, 0, 0, 0, 0, 0])
    s[0] += 1
    scr = pb.screen.ndarray
    t0 = time.perf_counter()
    if USE_SCROLL:
        img = wv.render(m, scr, scroll=pb.screen.tilemap_position_list[0][:2])
    else:
        img = wv.render(m, scr)
    render_time.append(time.perf_counter() - t0)
    if img is None:
        none_reasons[wv.last_reason.split(" (")[0]] = none_reasons.get(wv.last_reason.split(" (")[0], 0) + 1
        return
    s[1] += 1
    world = wv.world_image()
    valid = ~sprite_mask()
    pred = world[MY:MY + SCREEN_H, MX:MX + SCREEN_W, :3]
    eq = (pred == scr[:, :, :3]).all(axis=2)
    ok, tot = int((eq & valid).sum()), int(valid.sum())
    s[2] += ok
    s[3] += tot
    # also excluding text boxes/menus (wTileMap tiles >= $60, at rest only)
    txt = np.zeros((SCREEN_H, SCREEN_W), bool)
    if m[0xCFC4] == 0 and wv.screen_offset() == (0, 0):
        tm = np.array(m[0xC3A0:0xC3A0 + 360], np.uint8).reshape(18, 20) >= 0x60
        txt = np.kron(tm, np.ones((8, 8), bool))
    v2 = valid & ~txt
    s[4] += int((eq & v2).sum())
    s[5] += int(v2.sum())
    if wv.screen_offset() != (0, 0):
        walk_stats[0] += int((eq & v2).sum())
        walk_stats[1] += int(v2.sum())
        walk_stats[2] += 1
    if v2.sum() and (eq & v2).sum() / v2.sum() < 0.97:
        low_frames.append((f, cur, round((eq & v2).sum() / v2.sum(), 4), m[0xCFC4], wv.screen_offset()))
    # NPCs that are on screen: our sprite drawing vs the real frame
    # all NPCs drawn by the module (incl. on-screen ones) vs the real frame
    if 0 in wv._obj_pal:
        canvas = world.copy()
        canvas[..., 3] = 0
        visible = [n for n in wv.last_npcs if 0 <= n[1] - MX <= SCREEN_W - 16 and 0 <= n[2] - MY <= SCREEN_H - 16]
        wv.draw_npcs(canvas, wv._last_crop, visible, m[0xFFFC], include_onscreen=True)
        for slot, left, top, im, grass in visible:
            sl, st = left - MX, top - MY
            if (st, sl) not in oam_prev:
                continue                   # not shown in the displayed OAM
            sub = canvas[top:top + 16, left:left + 16]
            drawn = sub[..., 3] == 255
            got = scr[top - MY:top - MY + 16, left - MX:left - MX + 16, :3][drawn]
            okn = int((got == sub[..., :3][drawn]).all(axis=1).sum())
            npc_stats[0] += okn
            npc_stats[1] += int(drawn.sum())
            npc_stats[2] += 1
            if okn < 0.9 * drawn.sum() and len(npc_bad) < 4000:
                npc_bad.append((f, cur, slot, round(okn / drawn.sum(), 2), hex(im), left - MX, top - MY, m[0xCFC4]))
    # margin consistency vs older renders
    ox, oy = wv.origin()
    for hf, hmap, (hox, hoy), hworld, hconn in history:
        if not 20 <= f - hf <= 90:
            continue
        if hmap == cur:
            sx, sy = ox - hox, oy - hoy
        elif cur in hconn:
            ddx, ddy = hconn[cur]
            sx, sy = ox - (hox + ddx), oy - (hoy + ddy)
        else:
            continue
        # real screen pixel (r, c) is at wide pixel (MY+sy+r, MX+sx+c) of hworld
        r0, c0 = MY + sy, MX + sx
        rr = np.arange(SCREEN_H)[:, None] + r0
        cc = np.arange(SCREEN_W)[None, :] + c0
        inside = (rr >= 0) & (rr < hworld.shape[0]) & (cc >= 0) & (cc < hworld.shape[1])
        # only pixels that were in the *margin* of the old render
        old_centre = (rr >= MY) & (rr < MY + SCREEN_H) & (cc >= MX) & (cc < MX + SCREEN_W)
        sel = inside & ~old_centre & v2
        if sel.sum() < 200:
            continue
        rs, cs = np.nonzero(sel)
        old = hworld[rs + r0, cs + c0, :3]
        good = (old == scr[rs, cs, :3]).all(axis=1)
        # pixels whose map graphics changed meanwhile (water/flower animation)
        same_tile = (old == pred[rs, cs]).all(axis=1)
        mm = margin.setdefault((hmap, cur), [0, 0, 0, 0])
        mm[0] += int(good.sum())
        mm[1] += int(good.size)
        mm[2] += int((good & same_tile).sum())
        mm[3] += int(same_tile.sum())
        break
    history.append((f, cur, (ox, oy), world, conn_deltas()))
    while history and f - history[0][0] > 90:
        history.pop(0)
    for label, pred_fn in list(want_png.items()):
        if pred_fn():
            Image.fromarray(img).save(f"{OUT}/{label}.png")
            # also a 2x upscale for easy viewing
            Image.fromarray(img).resize((img.shape[1] * 2, img.shape[0] * 2), Image.NEAREST).save(f"{OUT}/{label}_2x.png")
            saved.append(f"{OUT}/{label}.png")
            del want_png[label]


w = Walker(pb, on_frame)


def mid_step(map_id):
    return lambda: m[0xD35D] == map_id and m[0xCFC4] in (3, 4) and wv.screen_offset() != (0, 0)


def run():
    w.tick(2)
    want_png["wv_bedroom"] = lambda: True
    w.go("right", 2); w.go("up", 6); w.go("right", 2)       # stairs
    w.wait(30)
    want_png["wv_house1f"] = lambda: m[0xD35D] == 0x25 and m[0xCFC4] == 0
    w.go("down", 6); w.go("left", 4); w.go("down", 2)       # out of the door
    w.wait(30)
    want_png["wv_pallet_door"] = lambda: m[0xD35D] == 0 and m[0xCFC4] == 0
    w.go("right", 5); w.go("up", 3)                          # towards Route 1
    want_png["wv_pallet_north_midstep"] = lambda: m[0xD35D] == 0 and m[0xD360] <= 3 and m[0xCFC4] == 4
    w.go("up", 1)
    w.wait(10)
    w.go("right", 8)                                         # east edge (no connection)
    want_png["wv_pallet_east_edge"] = lambda: m[0xD35D] == 0 and m[0xCFC4] == 0
    w.wait(5)
    w.go("down", 12)
    w.go("left", 6)
    w.go("down", 4)
    w.go("left", 8)
    want_png["wv_pallet_south"] = lambda: m[0xD35D] == 0 and m[0xCFC4] == 0
    w.wait(5)
    w.goto(17, 12); w.goto(18, 3)                           # east side
    w.goto(10, 2)
    w.go("up", 1); print("trigger at", w.pos())
    want_png["wv_oak_cutscene"] = lambda: m[0xD35D] == 0 and any(
        (l - MX < 0 or t - MY < 0 or l - MX + 16 > SCREEN_W or t - MY + 16 > SCREEN_H)
        and 0 <= l <= wv.out_w - 16 and 0 <= t <= wv.out_h - 16
        for _, l, t, _, _ in wv.last_npcs) and wv.last_reason == "ok" and frame_no[0] % 4 == 0
    w.go("up", 8); print("north", w.pos())     # Oak stops us: Pikachu battle
    w.go("down", 8); print("back", w.pos())
    for i in range(4000):
        if i % 500 == 0:
            print("mash", i, w.pos(), wv.last_reason)
        if i % 20 == 0:
            pb.button_press("a")
        if i % 20 == 3:
            pb.button_release("a")
        w.tick()
    print("after cutscene", w.pos())
    want_png["wv_oaks_lab"] = lambda: m[0xD35D] == 0x28 and m[0xCFC4] == 0 and wv.last_reason == "ok"
    w.wait(5)

    # phase 2: after getting Pikachu (pallet_after_lab.state was made from
    # room.state with progress.py: lab sequence + rival battle, then out of
    # the lab).  Pallet Town -> Route 1 (north connection) and back.
    pb.load_state(open(f"{OUT}/pallet_after_lab.state", "rb"))
    history.clear()
    w.tick(3)
    w.go("left", 3); w.go("up", 10); w.goto(10, 2)
    want_png["wv_route1_entry_midstep"] = lambda: m[0xD35D] == 0x0C and m[0xCFC4] in (3, 4) and m[0xC103] == 0xFF
    w.go("up", 10); print("route 1", w.pos())
    want_png["wv_route1"] = lambda: m[0xD35D] == 0x0C and m[0xCFC4] == 0 and wv.last_reason == "ok"
    w.go("left", 2); w.go("up", 4); w.go("down", 6); w.go("right", 2)
    want_png["wv_route1_south_edge"] = lambda: m[0xD35D] == 0x0C and m[0xD360] >= 34 and m[0xCFC4] == 0 and wv.last_reason == "ok"
    w.go("down", 12); print("back in Pallet", w.pos())
    w.go("right", 4); w.go("down", 4); w.go("left", 6); w.go("up", 6)
    w.go("up", 8); w.go("down", 8)
    w.wait(5)


run()

print("== centre match (map render vs real screen, sprite pixels excluded) ==")
names = {0x00: "Pallet Town", 0x25: "Red's house 1F", 0x26: "Red's house 2F", 0x28: "Oak's lab", 0x0C: "Route 1"}
tok = ttot = 0
t2ok = t2tot = 0
for k, (fr, rd, ok, tot, ok2, tot2) in sorted(stats.items()):
    tok += ok; ttot += tot; t2ok += ok2; t2tot += tot2
    print(f"map {k:#04x} {names.get(k, ''):15s} frames {fr:5d} rendered {rd:5d}  "
          f"match {100 * ok / max(tot, 1):7.3f}%  (text boxes excluded: {100 * ok2 / max(tot2, 1):7.3f}%)")
print(f"overall centre match {100 * tok / max(ttot, 1):.3f}%, excluding text boxes {100 * t2ok / max(t2tot, 1):.3f}%")
print(f"mid-step frames (fine scroll != 0): {walk_stats[2]}, match {100 * walk_stats[0] / max(walk_stats[1], 1):.3f}%")
print(f"on-screen NPC sprite check: {npc_stats[2]} sprite-frames, {100 * npc_stats[0] / max(npc_stats[1], 1):.2f}% of opaque pixels match")
print("NPC mismatches:", len(npc_bad), npc_bad[::max(1, len(npc_bad)//25)])
print("None reasons:", none_reasons)
print("frames below 97% (text boxes excluded):", len(low_frames), low_frames[:15])
print("== margin consistency (old wide render's margins vs later real screen) ==")
for (a, b), (ok, tot, ok2, tot2) in sorted(margin.items()):
    print(f"{a:#04x}->{b:#04x}: raw {100 * ok / max(tot, 1):.3f}% of {tot} px; "
          f"excluding animated/changed tiles {100 * ok2 / max(tot2, 1):.3f}% of {tot2} px")
rt = np.array(render_time) * 1000
print(f"render(): mean {rt.mean():.2f} ms, median {np.median(rt):.2f} ms, p99 {np.percentile(rt, 99):.2f} ms, max {rt.max():.1f} ms")
print("PNGs:", saved)
