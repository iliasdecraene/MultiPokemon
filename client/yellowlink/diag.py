"""Logging, crash capture and the copy/send report window."""
from __future__ import annotations

import collections
import faulthandler
import json
import logging
import os
import platform
import subprocess
import sys
import time
import traceback
import uuid
from pathlib import Path

from . import __version__

log = logging.getLogger("yellowlink")

MAX_REPORT_CHARS = 55000
KEEP_LOGS = 10


class RingHandler(logging.Handler):
    def __init__(self, size=4000):
        super().__init__()
        self.lines = collections.deque(maxlen=size)

    def emit(self, record):
        try:
            self.lines.append(self.format(record))
        except Exception:  # noqa: BLE001
            pass


class Diagnostics:
    """One per process. Owns the session log file and crash detection."""

    def __init__(self, base_dir: Path):
        self.dir = base_dir / "logs"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.marker = base_dir / "session.running"
        self.previous_crash_log: Path | None = None
        if self.marker.exists():
            try:
                p = Path(self.marker.read_text(encoding="utf-8").strip())
                if p.exists():
                    self.previous_crash_log = p
            except OSError:
                pass
        self.path = self.dir / time.strftime("yellowlink-%Y%m%d-%H%M%S.log")
        self.errors = 0
        self.ring = RingHandler()
        fmt = logging.Formatter("%(asctime)s.%(msecs)03d %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S")
        self.ring.setFormatter(fmt)
        self._file = open(self.path, "a", buffering=1, encoding="utf-8", errors="replace")
        fh = logging.StreamHandler(self._file)
        fh.setFormatter(fmt)
        root = logging.getLogger()
        root.setLevel(logging.INFO)
        for h in list(root.handlers):
            root.removeHandler(h)
        root.addHandler(fh)
        root.addHandler(self.ring)
        counter = _ErrorCounter(self)
        counter.setLevel(logging.ERROR)
        root.addHandler(counter)
        logging.captureWarnings(True)
        # Windowed exe has no console: route stray prints/stderr into the log.
        if sys.stdout is None:
            sys.stdout = self._file
        if sys.stderr is None:
            sys.stderr = self._file
        # Hard crashes (segfaults inside the emulator) get a native traceback in the file.
        try:
            faulthandler.enable(file=self._file, all_threads=True)
        except (RuntimeError, ValueError, OSError):
            pass
        sys.excepthook = self._excepthook
        try:
            self.marker.write_text(str(self.path), encoding="utf-8")
        except OSError:
            pass
        self._prune()
        log.info("YellowLink %s starting", __version__)
        log.info(system_info())

    def _excepthook(self, etype, value, tb):
        log.critical("unhandled exception:\n%s", "".join(traceback.format_exception(etype, value, tb)))

    def _prune(self):
        files = sorted(self.dir.glob("yellowlink-*.log"))
        for p in files[:-KEEP_LOGS]:
            try:
                p.unlink()
            except OSError:
                pass

    def clean_exit(self):
        try:
            self.marker.unlink()
        except OSError:
            pass

    def report_text(self, reason: str, extra: str = "") -> str:
        header = [f"YellowLink report — {reason}", f"version {__version__}", system_info()]
        if extra:
            header.append(extra)
        body = "\n".join(self.ring.lines)
        text = "\n".join(header) + "\n\n" + body
        if len(text) > MAX_REPORT_CHARS:
            text = "\n".join(header) + "\n\n[... older lines trimmed ...]\n" + body[-(MAX_REPORT_CHARS - 2000):]
        return text

    @staticmethod
    def file_tail(path: Path, reason: str) -> str:
        try:
            data = path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            data = f"(could not read {path}: {e})"
        if len(data) > MAX_REPORT_CHARS:
            data = "[... trimmed ...]\n" + data[-MAX_REPORT_CHARS:]
        return f"YellowLink report — {reason}\nlog file: {path.name}\n\n{data}"


class _ErrorCounter(logging.Handler):
    def __init__(self, diag):
        super().__init__()
        self.diag = diag

    def emit(self, record):
        self.diag.errors += 1


def system_info() -> str:
    parts = [f"{platform.system()} {platform.release()} ({platform.version()})",
             f"python {platform.python_version()}", f"frozen={getattr(sys, 'frozen', False)}"]
    for mod in ("pyboy", "pygame", "websockets", "numpy"):
        try:
            m = __import__(mod)
            parts.append(f"{mod} {getattr(m, '__version__', getattr(m, 'version', '?'))}")
        except Exception:  # noqa: BLE001
            parts.append(f"{mod} missing")
    return " | ".join(str(p) for p in parts)


def send_report_sync(cfg, text: str, reason: str) -> str:
    """Send a report over a short-lived connection (used when the game isn't connected)."""
    from websockets.sync.client import connect
    url = cfg.get("server_url", "")
    # Report-only hello: the server stores the report without logging us in.
    hello = {"type": "hello", "name": cfg.get("name") or "player", "uid": "report-" + str(uuid.uuid4()),
             "key": cfg.get("room_key", ""), "version": __version__, "protocol": 1, "mode": "report"}
    with connect(url, open_timeout=8, close_timeout=2, max_size=1 << 20) as ws:
        ws.send(json.dumps(hello))
        ws.send(json.dumps({"type": "bug_report", "text": text, "reason": reason}))
        deadline = time.time() + 8
        while time.time() < deadline:
            msg = json.loads(ws.recv(timeout=max(0.1, deadline - time.time())))
            if msg.get("type") == "report_saved":
                return "Sent! The host can find it on the server."
            if msg.get("type") == "error":
                return "Server said: " + msg.get("message", "error")
    return "No answer from the server."


def show_report_window(text: str, title: str, intro: str, send=None, log_dir: Path | None = None):
    """Blocking tkinter window with the report, Copy / Send / Open folder buttons."""
    try:
        import tkinter as tk
        from tkinter import scrolledtext
    except ImportError:
        return
    root = tk.Tk()
    root.title(title)
    root.geometry("760x520")
    tk.Label(root, text=intro, justify="left", anchor="w", wraplength=720).pack(fill="x", padx=10, pady=(10, 4))
    box = scrolledtext.ScrolledText(root, wrap="none", font=("Consolas", 9))
    box.insert("1.0", text)
    box.configure(state="disabled")
    box.see("end")
    box.pack(fill="both", expand=True, padx=10, pady=4)
    status = tk.Label(root, text="", anchor="w", fg="#2a7")
    status.pack(fill="x", padx=10)
    btns = tk.Frame(root)
    btns.pack(fill="x", padx=10, pady=10)

    def copy():
        root.clipboard_clear()
        root.clipboard_append(text)
        root.update()
        status.config(text="Copied — paste it into a message to the host.")

    def do_send():
        status.config(text="Sending…")
        root.update()
        try:
            status.config(text=send())
        except Exception as e:  # noqa: BLE001
            status.config(text=f"Couldn't send ({e}). Use Copy instead.", fg="#c33")

    def open_folder():
        if not log_dir:
            return
        try:
            if sys.platform == "win32":
                os.startfile(str(log_dir))  # noqa: S606
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(log_dir)])
            else:
                subprocess.Popen(["xdg-open", str(log_dir)])
        except OSError:
            pass

    tk.Button(btns, text="Copy to clipboard", width=18, command=copy).pack(side="left")
    if send:
        tk.Button(btns, text="Send to host", width=14, command=do_send).pack(side="left", padx=6)
    if log_dir:
        tk.Button(btns, text="Open log folder", width=15, command=open_folder).pack(side="left")
    tk.Button(btns, text="Close", width=10, command=root.destroy).pack(side="right")
    root.mainloop()
