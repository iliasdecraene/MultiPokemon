"""Merge one server's data into another (two groups of friends become one). SPEC.md section 8.

    python -m yellowlink_server.merge <from-data-dir> <into-data-dir> [--dry-run]

Copies players (players.json), PC backups (pc/<uid>.json), Soul Link records (challenges.json), badges and reports from
FROM into INTO. Nothing in INTO is overwritten: a player id or challenge id that exists in both is kept as it is in
INTO and reported. Player ids never repeat across servers (every server gets its own id per player), so in practice
nothing collides. INTO is backed up first (<into>.before-merge-<time>). Both servers must be STOPPED.
Refuses when FROM has a trade still being finished (pc/journal not empty): start and stop that server once
(it finishes pending trades on start-up), then run this again.
Pure standard library, so it also runs with the host's python3 (no Docker needed).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time


def _load(path, default):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return default


def _write(path, data, dry):
    if dry:
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".merge-tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def merge(src: str, dst: str, dry: bool = False, backup: bool = True) -> dict:
    """Returns a report dict: added/kept counts and notes. Raises SystemExit with a message on refusal."""
    src, dst = os.path.abspath(src), os.path.abspath(dst)
    if src == dst:
        raise SystemExit("FROM and INTO are the same folder")
    if not os.path.isdir(src):
        raise SystemExit(f"{src} doesn't exist")
    journal = os.path.join(src, "pc", "journal")
    if os.path.isdir(journal) and any(n.endswith(".json") for n in os.listdir(journal)):
        raise SystemExit(f"{src} still has a trade being finished ({journal}). Start that server once, stop it "
                         "again, then run the merge.")
    report = {"players_added": [], "players_kept": [], "pc_added": 0, "pc_kept": [], "challenges_added": 0,
              "challenges_kept": [], "reports_added": 0, "badges_added": 0, "same_names": [], "backup": None}
    if backup and not dry and os.path.isdir(dst):
        bak = f"{dst}.before-merge-{time.strftime('%Y%m%d-%H%M%S')}"
        shutil.copytree(dst, bak)
        report["backup"] = bak

    # players
    sp = _load(os.path.join(src, "players.json"), {})
    dp = _load(os.path.join(dst, "players.json"), {})
    s_players = sp.get("players", sp) if isinstance(sp, dict) else {}
    d_players = dp.get("players", dp) if isinstance(dp, dict) else {}
    names_in_dst = {str(p.get("name", "")).strip().lower() for p in d_players.values() if isinstance(p, dict)}
    for uid, p in s_players.items():
        if not isinstance(p, dict):
            continue
        if uid in d_players:
            report["players_kept"].append(uid)
            continue
        d_players[uid] = p
        report["players_added"].append(f"{p.get('name', '?')} ({uid[:8]})")
        if str(p.get("name", "")).strip().lower() in names_in_dst:
            report["same_names"].append(p.get("name", "?"))
    _write(os.path.join(dst, "players.json"), {"version": 1, "players": d_players}, dry)

    # PC backups (pc/<uid>.json; the journal folder is skipped - checked empty above)
    spc = os.path.join(src, "pc")
    if os.path.isdir(spc):
        for name in sorted(os.listdir(spc)):
            path = os.path.join(spc, name)
            if not name.endswith(".json") or not os.path.isfile(path):
                continue
            target = os.path.join(dst, "pc", name)
            if os.path.exists(target):
                report["pc_kept"].append(name)
                continue
            if not dry:
                os.makedirs(os.path.dirname(target), exist_ok=True)
                shutil.copy2(path, target)
            report["pc_added"] += 1

    # Soul Link records
    sc = _load(os.path.join(src, "challenges.json"), {})
    dc = _load(os.path.join(dst, "challenges.json"), {})
    s_recs = sc.get("challenges", []) if isinstance(sc, dict) else []
    d_recs = dc.get("challenges", []) if isinstance(dc, dict) else []
    have = {r.get("id") for r in d_recs if isinstance(r, dict)}
    for r in s_recs:
        if not isinstance(r, dict) or not r.get("id"):
            continue
        if r["id"] in have:
            report["challenges_kept"].append(r["id"])
            continue
        d_recs.append(r)
        have.add(r["id"])
        report["challenges_added"] += 1
    if s_recs:
        _write(os.path.join(dst, "challenges.json"), {"version": 1, "challenges": d_recs}, dry)

    # badges (SPEC.md section 9): per player id, like players
    sb = _load(os.path.join(src, "badges.json"), {})
    db = _load(os.path.join(dst, "badges.json"), {})
    s_b = sb.get("players", {}) if isinstance(sb, dict) else {}
    d_b = db.get("players", {}) if isinstance(db, dict) else {}
    for uid, b in s_b.items():
        if isinstance(b, dict) and uid not in d_b:
            d_b[uid] = b
            report["badges_added"] += 1
    if s_b:
        _write(os.path.join(dst, "badges.json"), {"version": 1, "players": d_b}, dry)

    # problem reports (F3)
    srep = os.path.join(src, "reports")
    if os.path.isdir(srep):
        for name in os.listdir(srep):
            target = os.path.join(dst, "reports", name)
            if os.path.isfile(os.path.join(srep, name)) and not os.path.exists(target):
                if not dry:
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    shutil.copy2(os.path.join(srep, name), target)
                report["reports_added"] += 1
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m yellowlink_server.merge",
                                 description="Merge one YellowLink server's data into another (both stopped).")
    ap.add_argument("src", help="data folder of the server that goes away (e.g. /opt/yellowlink2/server/data)")
    ap.add_argument("dst", help="data folder of the server that stays (e.g. /opt/yellowlink/server/data)")
    ap.add_argument("--dry-run", action="store_true", help="only show what would happen")
    a = ap.parse_args(argv)
    r = merge(a.src, a.dst, dry=a.dry_run)
    print(("DRY RUN - nothing written\n" if a.dry_run else "") +
          f"players added: {len(r['players_added'])} {r['players_added']}\n"
          f"PC backups added: {r['pc_added']}\n"
          f"Soul Link records added: {r['challenges_added']}\n"
          f"badges added: {r['badges_added']} players\n"
          f"reports added: {r['reports_added']}")
    if r["players_kept"] or r["pc_kept"] or r["challenges_kept"]:
        print(f"already there, kept as they were: players {r['players_kept']}, PC {r['pc_kept']}, "
              f"challenges {r['challenges_kept']}")
    if r["same_names"]:
        print(f"note: these names now exist twice (different saves of the same person?): {r['same_names']}")
    if r["backup"]:
        print(f"backup of the old data: {r['backup']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
