"""Self-update for the Windows exe: download, verify, swap, restart.

Windows lets a running .exe be *renamed* (not deleted), so we move the running
YellowLink.exe aside to YellowLink.old.exe, put the new one in its place, start it,
and exit. The next start deletes the .old file.
"""
from __future__ import annotations

import hashlib
import logging
import os
import subprocess
import sys
import threading
import urllib.request
from pathlib import Path

log = logging.getLogger("yellowlink.update")

CHUNK = 1 << 16


def exe_path() -> Path | None:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve()
    return None


def can_self_update() -> bool:
    exe = exe_path()
    return bool(exe and sys.platform == "win32" and os.access(exe.parent, os.W_OK))


def asset_urls(download_url: str, version: str):
    """From '.../releases/latest' derive the exact exe + checksum URLs for a version."""
    base = download_url.rstrip("/")
    if base.endswith("/latest"):
        base = base[: -len("/latest")]
    if not base.endswith("/releases"):
        return None
    tag = "v" + version.lstrip("v")
    return f"{base}/download/{tag}/YellowLink.exe", f"{base}/download/{tag}/YellowLink.exe.sha256"


def cleanup_old():
    exe = exe_path()
    if not exe:
        return
    for name in ("YellowLink.old.exe", "YellowLink.update.tmp"):
        p = exe.with_name(name)
        if p.exists():
            try:
                p.unlink()
                log.info("removed %s", p.name)
            except OSError:
                pass


class Download:
    """Background download with progress; state is read by the game loop."""

    def __init__(self, url: str, sha_url: str, version: str):
        self.url, self.sha_url, self.version = url, sha_url, version
        self.done = 0
        self.total = 0
        self.status = "downloading"   # downloading | ready | failed
        self.error = ""
        self.path: Path | None = None
        threading.Thread(target=self._run, name="yl-update", daemon=True).start()

    @property
    def fraction(self) -> float:
        return self.done / self.total if self.total else 0.0

    def _run(self):
        exe = exe_path()
        tmp = exe.with_name("YellowLink.update.tmp")
        try:
            with urllib.request.urlopen(self.sha_url, timeout=20) as r:
                expected = r.read().decode("ascii", "replace").split()[0].strip().lower()
            if len(expected) != 64:
                raise ValueError("bad checksum file")
            h = hashlib.sha256()
            with urllib.request.urlopen(self.url, timeout=30) as r, open(tmp, "wb") as out:
                self.total = int(r.headers.get("Content-Length") or 0)
                while True:
                    chunk = r.read(CHUNK)
                    if not chunk:
                        break
                    out.write(chunk)
                    h.update(chunk)
                    self.done += len(chunk)
            if h.hexdigest() != expected:
                raise ValueError("download was corrupted (checksum mismatch)")
            self.path = tmp
            self.status = "ready"
            log.info("update %s downloaded and verified (%d bytes)", self.version, self.done)
        except Exception as e:  # noqa: BLE001
            self.status = "failed"
            self.error = str(e) or e.__class__.__name__
            log.warning("update download failed: %s", self.error)
            try:
                tmp.unlink()
            except OSError:
                pass


def install_and_restart(new_file: Path):
    """Swap the exe and start the new one. Caller must exit right after."""
    exe = exe_path()
    old = exe.with_name("YellowLink.old.exe")
    if old.exists():
        old.unlink()
    os.replace(exe, old)          # renaming the running exe is allowed on Windows
    try:
        os.replace(new_file, exe)
    except OSError:
        os.replace(old, exe)      # put the original back
        raise
    flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    # Start the new exe as a fresh, independent PyInstaller app (not a child of this one).
    env = {k: v for k, v in os.environ.items() if not k.startswith(("_MEI", "_PYI"))}
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    subprocess.Popen([str(exe)], creationflags=flags, close_fds=True, env=env, cwd=str(exe.parent))
    log.info("started updated exe")
