"""Run the preserved protocol handlers inside the phone's Python worker."""

import asyncio
import json
import logging
from contextlib import nullcontext
from pathlib import Path
from js import persistFiles, postProtocol

from server import server as game
from desktop.saves import create_save, export_save, import_save, save_path

ROOT = Path("/persistent")
sockets = {}
tasks = {}
state = None
edition = "normal"


def configure(which):
    global edition, state
    edition = which
    game.GALLERY_MODE = which == "gallery"
    game.DATA_DIR = ROOT / which
    game.DATA_DIR.mkdir(parents=True, exist_ok=True)
    game.STATE_FILE = save_path(ROOT, which)
    game.BACKUP_DIR = game.DATA_DIR / "backups"
    game.UNKNOWN_LOG = game.DATA_DIR / "unknown_commands.jsonl"
    game.LOG_FILE = game.DATA_DIR / "server.log"
    game.BACKUP_INTERVAL_SECONDS = 1800
    # A single worker owns the IndexedDB save; native file locks aren't available in WASM.
    game.state_process_lock = lambda *args: nullcontext()
    logging.basicConfig(level=logging.WARNING)
    state = json.loads(game.STATE_FILE.read_text(encoding="utf-8")) if game.STATE_FILE.exists() else None
    return info()


def info():
    name = game.get_role_parts(state)[1].get("name", "呱呱") if state is not None else ""
    return json.dumps({"exists": state is not None, "name": name, "edition": edition}, ensure_ascii=False)


async def create(name="呱呱"):
    global state
    create_save(ROOT, edition, name)
    state = json.loads(game.STATE_FILE.read_text(encoding="utf-8"))
    await persistFiles()
    return info()


async def load_file(name):
    global state
    for task in list(tasks.values()):
        task.cancel()
    if tasks:
        await asyncio.gather(*list(tasks.values()), return_exceptions=True)
    sockets.clear()
    tasks.clear()
    import_save(Path("/tmp") / name, ROOT, edition)
    state = json.loads(game.STATE_FILE.read_text(encoding="utf-8"))
    await persistFiles()
    return info()


async def export():
    target = Path("/tmp/progress.frogsave")
    export_save(game.STATE_FILE, target, edition)
    return target.read_bytes()


class LocalSocket:
    def __init__(self, identifier):
        self.identifier = identifier
        self.remote_address = ("phone", 0)
        self.queue = asyncio.Queue()

    def __aiter__(self):
        return self

    async def __anext__(self):
        await persistFiles()
        message = await self.queue.get()
        if message is None:
            raise StopAsyncIteration
        return message

    async def send(self, message):
        await persistFiles()
        postProtocol(self.identifier, message)


async def serve(identifier):
    socket = LocalSocket(identifier)
    sockets[identifier] = socket
    tasks[identifier] = asyncio.current_task()
    await game.serve_client(socket, state)
    sockets.pop(identifier, None)
    tasks.pop(identifier, None)
    await persistFiles()


def receive(identifier, message):
    if identifier in sockets:
        sockets[identifier].queue.put_nowait(message)


def disconnect(identifier):
    if identifier in sockets:
        sockets[identifier].queue.put_nowait(None)
