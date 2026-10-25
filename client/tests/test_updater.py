import hashlib
import http.server
import os
import sys
import threading
import time
from functools import partial

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from yellowlink import updater  # noqa: E402


def test_asset_urls():
    exe, sha = updater.asset_urls("https://github.com/iliasdecraene/MultiPokemon/releases/latest", "0.3.0")
    assert exe == "https://github.com/iliasdecraene/MultiPokemon/releases/download/v0.3.0/YellowLink.exe"
    assert sha.endswith("/download/v0.3.0/YellowLink.exe.sha256")
    assert updater.asset_urls("https://example.com/whatever", "1.0.0") is None


def _serve(folder):
    handler = partial(http.server.SimpleHTTPRequestHandler, directory=str(folder))
    handler.log_message = lambda *a: None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _wait(dl):
    for _ in range(200):
        if dl.status != "downloading":
            return
        time.sleep(0.02)


def test_download_verify_and_swap(tmp_path, monkeypatch):
    web = tmp_path / "web"
    web.mkdir()
    new_exe = os.urandom(300_000)
    (web / "YellowLink.exe").write_bytes(new_exe)
    (web / "YellowLink.exe.sha256").write_text(hashlib.sha256(new_exe).hexdigest() + "  dist/YellowLink.exe\n")
    (web / "bad.sha256").write_text("0" * 64 + "  x\n")
    srv = _serve(web)
    base = f"http://127.0.0.1:{srv.server_address[1]}"

    app_dir = tmp_path / "app"
    app_dir.mkdir()
    running = app_dir / "YellowLink.exe"
    running.write_bytes(b"old version")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(running))

    bad = updater.Download(f"{base}/YellowLink.exe", f"{base}/bad.sha256", "9.9.9")
    _wait(bad)
    assert bad.status == "failed" and "checksum" in bad.error
    assert not (app_dir / "YellowLink.update.tmp").exists()

    dl = updater.Download(f"{base}/YellowLink.exe", f"{base}/YellowLink.exe.sha256", "9.9.9")
    _wait(dl)
    assert dl.status == "ready" and dl.fraction == 1.0

    launched = {}
    monkeypatch.setattr(updater.subprocess, "Popen", lambda args, **kw: launched.update(args=args, env=kw["env"]))
    monkeypatch.setenv("_PYI_APPLICATION_HOME_DIR", "x")
    updater.install_and_restart(dl.path)
    assert running.read_bytes() == new_exe
    assert (app_dir / "YellowLink.old.exe").read_bytes() == b"old version"
    assert launched["args"] == [str(running)]
    assert "_PYI_APPLICATION_HOME_DIR" not in launched["env"]
    assert launched["env"]["PYINSTALLER_RESET_ENVIRONMENT"] == "1"

    updater.cleanup_old()
    assert not (app_dir / "YellowLink.old.exe").exists()
    srv.shutdown()
