"""Entry point: python -m yellowlink_server --config config.json [--host H] [--port P]"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys

from . import __version__
from .config import load_config
from .server import YellowLinkServer


def parse_args(argv=None):
    p = argparse.ArgumentParser(prog="yellowlink_server", description="YellowLink relay server")
    p.add_argument("--config", help="path to config.json (optional; falls back to $YL_CONFIG if that file exists)")
    p.add_argument("--host", help="bind address (default from config, 0.0.0.0)")
    p.add_argument("--port", type=int, help="port (default from config, 8765)")
    return p.parse_args(argv)


async def run(cfg) -> None:
    server = YellowLinkServer(cfg)
    await server.start()
    log = logging.getLogger("yellowlink")
    log.info("YellowLink server %s listening on %s:%d (data dir: %s)",
             __version__, cfg.host, server.port, os.path.abspath(cfg.data_dir))
    if cfg.key == "change-me":
        log.warning("room key is still 'change-me' -- set YL_KEY or 'key' in config.json!")
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError):
            pass  # e.g. Windows
    try:
        await stop.wait()
    finally:
        log.info("shutting down")
        await server.stop()


def main(argv=None) -> int:
    logging.basicConfig(
        level=os.environ.get("YL_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    logging.getLogger("websockets").setLevel(logging.WARNING)
    args = parse_args(argv)
    config_path = args.config
    if config_path is None:
        # Optional fallback (used by the Docker image): YL_CONFIG=/data/config.json
        env_path = os.environ.get("YL_CONFIG")
        if env_path and os.path.isfile(env_path):
            config_path = env_path
    try:
        cfg = load_config(config_path, host=args.host, port=args.port)
    except (OSError, ValueError) as exc:
        logging.getLogger("yellowlink").error("could not load config: %s", exc)
        return 2
    try:
        asyncio.run(run(cfg))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
