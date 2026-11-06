"""Run the packaged desktop service and portable save tools on macOS."""

import argparse
import json
import socket
import sys
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from websockets.sync.client import connect

from desktop.saves import create_save, export_save, import_save, save_path
from server import server as game


class GameFiles(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def serve(root, edition, assets):
    save = save_path(root, edition)
    if not save.exists():
        create_save(root, edition)
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    http = ThreadingHTTPServer(("127.0.0.1", 0), partial(GameFiles, directory=str(assets)))
    url = (f"http://127.0.0.1:{http.server_port}/index.html?edition={edition}"
           f"&serverPort={port}&desktopApp=1")

    def web_when_ready():
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                with connect(f"ws://127.0.0.1:{port}", open_timeout=0.4, proxy=None) as ws:
                    ws.send(json.dumps({"cmd": "offline.info", "session": 1}))
                    ready = json.loads(ws.recv(timeout=1))["data"]["edition"] == edition
                if ready:
                    print(url, flush=True)
                    http.serve_forever()
                    return
            except (OSError, TimeoutError):
                time.sleep(0.12)
        print("本地游戏服务启动超时。", file=sys.stderr, flush=True)

    threading.Thread(target=web_when_ready, daemon=True).start()
    sys.argv = [sys.argv[0], "--data-dir", str(save.parent), "--port", str(port)]
    if edition == "gallery":
        sys.argv.append("--gallery")
    game.main()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("edition", choices=("normal", "gallery"))
    parser.add_argument("data_root", type=Path)
    parser.add_argument("action", choices=("play", "import", "export"))
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    try:
        if args.action == "play":
            serve(args.data_root, args.edition, args.path)
        elif args.action == "import":
            import_save(args.path, args.data_root, args.edition)
        else:
            export_save(save_path(args.data_root, args.edition), args.path, args.edition)
    except (ValueError, RuntimeError, OSError) as error:
        print(str(error), file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
