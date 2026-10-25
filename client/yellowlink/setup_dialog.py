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
    row(2, "Server address", "server_url")
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
            messagebox.showerror("YellowLink", "That file isn't a supported English Pokémon Red, Blue, Yellow,\n"
                                               "Gold, Silver or Crystal game file.")
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
