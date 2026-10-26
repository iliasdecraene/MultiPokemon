"""First-run / settings window (tkinter, ships with Python on Windows)."""
from __future__ import annotations

import shutil
import time
from pathlib import Path

from . import library, text
from .config import app_dir


def run_setup(cfg, first_run: bool) -> bool:
    """Show the settings form. Returns True if the user saved valid settings."""
    import tkinter as tk
    from tkinter import filedialog, messagebox

    result = {"ok": False}
    root = tk.Tk()
    root.title("YellowLink – setup" if first_run else "YellowLink – settings")
    root.resizable(False, False)
    pad = {"padx": 8, "pady": 4}

    fields = {}

    def row(r, label, key, show=None, width=42):
        tk.Label(root, text=label, anchor="w").grid(row=r, column=0, sticky="w", **pad)
        var = tk.StringVar(value=str(cfg.get(key, "")))
        e = tk.Entry(root, textvariable=var, width=width, show=show)
        e.grid(row=r, column=1, sticky="we", **pad)
        fields[key] = var
        return e

    tk.Label(root, text="Multiplayer Pokémon — you bring your own game files.",
             font=("Segoe UI", 10, "bold")).grid(row=0, column=0, columnspan=3, sticky="w", **pad)
    row(1, "Your name (shown to friends)", "name")
    several = len(getattr(cfg, "servers", None) or []) > 1
    row(2, "Server address" + (" (this server)" if several else ""), "server_url")
    row(3, "Room password", "room_key", show="•")
    row(4, "Your game file (.gb / .gbc)", "rom_path")

    def browse_rom():
        p = filedialog.askopenfilename(title="Select your Pokémon Yellow ROM",
                                       filetypes=[("Game Boy ROM", "*.gb *.gbc"), ("All files", "*.*")])
        if p:
            fields["rom_path"].set(p)

    tk.Button(root, text="Browse…", command=browse_rom).grid(row=4, column=2, **pad)

    tk.Label(root, text="Import an existing save (.sav) — optional", anchor="w").grid(row=5, column=0, sticky="w", **pad)
    sav_var = tk.StringVar(value="")
    tk.Entry(root, textvariable=sav_var, width=42).grid(row=5, column=1, sticky="we", **pad)

    def browse_sav():
        p = filedialog.askopenfilename(title="Select a Pokémon Yellow save file",
                                       filetypes=[("Save file", "*.sav *.srm *.ram"), ("All files", "*.*")])
        if p:
            sav_var.set(p)

    tk.Button(root, text="Browse…", command=browse_sav).grid(row=5, column=2, **pad)
    skip_var = tk.BooleanVar(value=bool(cfg.get("skip_intro", True)))
    tk.Checkbutton(root, text="Skip the intro and continue my save automatically", variable=skip_var).grid(
        row=6, column=0, columnspan=3, sticky="w", **pad)
    tk.Label(root, text="Saves live in: " + str(cfg.save_path.parent), fg="#666").grid(
        row=7, column=0, columnspan=3, sticky="w", **pad)

    def on_ok():
        name = text.sanitize_name(fields["name"].get())
        if not name:
            messagebox.showerror("YellowLink", "Please enter a name (letters, numbers, spaces; max 10).")
            return
        url = fields["server_url"].get().strip()
        if not (url.startswith("ws://") or url.startswith("wss://")):
            url = "ws://" + url
        rom = fields["rom_path"].get().strip().strip('"')
        if not rom or not Path(rom).is_file():
            messagebox.showerror("YellowLink", "Please select your Pokémon game file (.gb/.gbc).")
            return
        info = library.identify(rom)
        if info is None:
            messagebox.showerror("YellowLink", "That file isn't English Pokémon Yellow or Crystal.\n"
                                               "Those are the two games YellowLink supports.")
            return
        sav = sav_var.get().strip().strip('"')
        if sav:
            p = Path(sav)
            if not p.is_file() or p.stat().st_size < 32768:
                messagebox.showerror("YellowLink", "That save file looks invalid (needs to be 32 KB).")
                return
            cfg["current_game"] = info.id
            try:
                cfg.resume_path.unlink()   # an imported save replaces the resume snapshot
            except OSError:
                pass
            if cfg.save_path.exists():
                shutil.copy2(cfg.save_path, cfg.backup_dir / f"before-import-{int(time.time())}.sav")
            with open(p, "rb") as f:
                data = f.read(32768)
            cfg.save_path.write_bytes(data)
        cfg["name"] = name
        cfg["server_url"] = url
        cfg["room_key"] = fields["room_key"].get()
        lib = library.Library(cfg, app_dir())
        try:
            lib.add_rom(rom)
        except (ValueError, OSError) as e:
            messagebox.showerror("YellowLink", str(e))
            return
        lib.set_current(info.id)
        cfg["skip_intro"] = bool(skip_var.get())
        cfg.save()
        result["ok"] = True
        root.destroy()

    btns = tk.Frame(root)
    btns.grid(row=8, column=0, columnspan=3, sticky="e", **pad)
    tk.Button(btns, text="Cancel", width=10, command=root.destroy).pack(side="right", padx=4)
    tk.Button(btns, text="Save & play", width=12, command=on_ok, default="active").pack(side="right", padx=4)
    root.bind("<Return>", lambda e: on_ok())
    root.mainloop()
    return result["ok"]


def server_form(entry, check):
    """Add/edit-a-server window (runs inside the game, like the game-file picker).

    entry: the server being edited, or None to add one. check(label, url, key) saves it and returns an error
    message or None. Returns None when cancelled, else {"join": bool} (join: switch to the new server now)."""
    import tkinter as tk
    from tkinter import messagebox

    result = {"value": None}
    root = tk.Tk()
    root.title("YellowLink – " + ("edit server" if entry else "add a server"))
    root.resizable(False, False)
    root.attributes("-topmost", True)
    pad = {"padx": 8, "pady": 4}
    tk.Label(root, text="Each server has its own saves, PC and challenges.",
             font=("Segoe UI", 10, "bold")).grid(row=0, column=0, columnspan=2, sticky="w", **pad)
    values = {}
    rows = [("Name (optional – for you)", "label", None), ("Server address", "url", None),
            ("Room password", "key", "•")]
    first = None
    for i, (label, key, show) in enumerate(rows, start=1):
        tk.Label(root, text=label, anchor="w").grid(row=i, column=0, sticky="w", **pad)
        var = tk.StringVar(value=str((entry or {}).get(key, "")))
        e = tk.Entry(root, textvariable=var, width=40, show=show)
        e.grid(row=i, column=1, sticky="we", **pad)
        values[key] = var
        first = first or e
    tk.Label(root, text="Leave the name empty to use the server's own name. Ask the host for the address "
                        "(like ws://1.2.3.4:8766) and password.", fg="#666", wraplength=430, justify="left").grid(
        row=4, column=0, columnspan=2, sticky="w", **pad)
    join = tk.BooleanVar(value=True)
    if not entry:
        tk.Checkbutton(root, text="Switch to it now (your current game is saved first)", variable=join).grid(
            row=5, column=0, columnspan=2, sticky="w", **pad)

    def on_ok(*_):
        err = check(values["label"].get(), values["url"].get(), values["key"].get())
        if err:
            messagebox.showerror("YellowLink", err, parent=root)
            return
        result["value"] = {"join": bool(join.get()) and not entry}
        root.destroy()

    btns = tk.Frame(root)
    btns.grid(row=6, column=0, columnspan=2, sticky="e", **pad)
    tk.Button(btns, text="Cancel", width=10, command=root.destroy).pack(side="right", padx=4)
    tk.Button(btns, text="Save" if entry else "Add server", width=12, command=on_ok, default="active").pack(
        side="right", padx=4)
    root.bind("<Return>", on_ok)
    root.bind("<Escape>", lambda e: root.destroy())
    first.focus_set()
    root.mainloop()
    return result["value"]
