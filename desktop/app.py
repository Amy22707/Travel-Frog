"""Direct desktop entry for one edition of Travel Frog."""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox

from desktop.launcher import CREATE_NO_WINDOW, ROOT, FROZEN, find_browser
from desktop.saves import create_save, export_save, import_save, save_path

CREAM = "#f6f1de"
GREEN = "#526b42"
INK = "#414b37"
MUTED = "#81866e"


class DesktopGame:
    def __init__(self, root, edition, data_root=None):
        self.root, self.edition = root, edition
        self.data_root = (data_root or ROOT / "user-data").resolve()
        self.path = save_path(self.data_root, edition)
        self.title = "旅行青蛙·中国之旅" + ("图鉴版" if edition == "gallery" else "")
        self.service = self.http = self.logfile = None
        self.game_open = self.busy = False
        self.last_ping = 0
        self.closed_at = 0
        self.status = tk.StringVar()
        root.title(self.title)
        root.configure(bg=CREAM)
        root.resizable(False, False)
        root.geometry("530x520")
        root.protocol("WM_DELETE_WINDOW", self.close)
        icon = ROOT / "icon.png" if FROZEN else ROOT / "release/TravelFrog/icon.png"
        if icon.exists():
            self.icon = tk.PhotoImage(file=str(icon))
            root.iconphoto(True, self.icon)
            tk.Label(root, image=self.icon, bg=CREAM).pack(pady=(32, 12))
        tk.Label(root, text="旅行青蛙", font=("Microsoft YaHei UI", 25, "bold"), fg=INK, bg=CREAM).pack()
        tk.Label(root, text="中 国 之 旅" + ("  ·  图鉴版" if edition == "gallery" else ""),
                 font=("Microsoft YaHei UI", 11), fg=MUTED, bg=CREAM).pack(pady=(4, 16))
        tk.Label(root, textvariable=self.status, font=("Microsoft YaHei UI", 10), fg=INK, bg=CREAM,
                 wraplength=440).pack(pady=(0, 20))
        self.button("继续旅程" if self.path.exists() else "开始新的旅程", self.play, GREEN, "white")
        row = tk.Frame(root, bg=CREAM)
        row.pack(pady=16)
        for label, fn in (("导入存档", self.import_file), ("导出存档", self.export_file), ("存档文件夹", self.open_folder)):
            tk.Button(row, text=label, command=fn, bg=CREAM, fg=GREEN, relief="flat", borderwidth=0,
                      font=("Microsoft YaHei UI", 10), cursor="hand2", padx=15, pady=8).pack(side="left")
        tk.Label(root, text="在自己的小世界里，慢慢旅行。", font=("Microsoft YaHei UI", 9),
                 fg=MUTED, bg=CREAM).pack(side="bottom", pady=18)
        self.update_status()
        root.after(1500, self.watch)

    def button(self, text, fn, bg, fg):
        tk.Button(self.root, text=text, command=fn, bg=bg, fg=fg, activebackground="#637e50",
                  activeforeground=fg, relief="flat", borderwidth=0, font=("Microsoft YaHei UI", 12, "bold"),
                  cursor="hand2", width=29, pady=13).pack()

    def update_status(self):
        if self.path.exists():
            try:
                state = json.loads(self.path.read_text(encoding="utf-8"))
                role = next(row["data"] for row in state["sync_pushes"] if row["cmd"] == "client.load_role")
                self.status.set(f"{role['frog']['name']}的小小旅程，等你回来。")
            except (OSError, ValueError, KeyError, StopIteration):
                self.status.set("请导入一份完整存档，继续你的旅程。")
        else:
            self.status.set("开启一段新的旅程，或带上你原来的存档。")

    def run(self, fn):
        if self.busy:
            return
        self.busy = True
        def work():
            try:
                fn()
            except Exception as exc:
                error = str(exc)
                self.root.after(0, self.root.deiconify)
                self.root.after(0, lambda: messagebox.showerror(self.title, error))
            finally:
                self.busy = False
                self.root.after(0, self.update_status)
        threading.Thread(target=work, daemon=True).start()

    def play(self):
        self.run(self.open_game)

    def open_game(self):
        if self.game_open:
            self.root.after(0, self.root.withdraw)
            return
        if not self.path.exists():
            self.status.set("正在准备你的旅程……")
            create_save(self.data_root, self.edition)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # An available port lets the packaged games coexist with a developer server.
        with socket.socket() as port_socket:
            port_socket.bind(("127.0.0.1", 0))
            self.port = port_socket.getsockname()[1]
        executable = [str(ROOT / "FrogServer.exe")] if FROZEN else [sys.executable, "-m", "desktop.server_entry"]
        args = executable + ["--data-dir", str(self.path.parent), "--port", str(self.port)]
        if self.edition == "gallery":
            args.append("--gallery")
        self.logfile = (self.path.parent / "desktop.log").open("a", encoding="utf-8")
        self.service = subprocess.Popen(args, cwd=ROOT, stdout=self.logfile, stderr=self.logfile,
                                        creationflags=CREATE_NO_WINDOW)
        from websockets.sync.client import connect
        ready = False
        for _ in range(100):
            if self.service.poll() is not None:
                break
            try:
                with connect(f"ws://127.0.0.1:{self.port}", open_timeout=0.3, proxy=None) as ws:
                    ws.send(json.dumps({"cmd": "offline.info", "session": 1}))
                    ready = json.loads(ws.recv(timeout=1))["data"]["edition"] == self.edition
                if ready:
                    break
            except (OSError, TimeoutError):
                time.sleep(0.15)
        if not ready:
            self.stop_service()
            raise RuntimeError(f"游戏未能启动。请查看 {self.path.parent / 'desktop.log'}")
        directory = ROOT / "web/game" if FROZEN else ROOT / "release/TravelFrog/web/game"
        app = self
        class Files(SimpleHTTPRequestHandler):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, directory=str(directory), **kwargs)

            def do_POST(self):
                if self.path == "/__desktop/ping":
                    app.last_ping, app.closed_at = time.monotonic(), 0
                elif self.path == "/__desktop/closed":
                    app.closed_at = time.monotonic()
                elif self.path == "/__desktop/saves":
                    app.root.after(0, app.root.deiconify)
                else:
                    self.send_error(404)
                    return
                self.send_response(204)
                self.end_headers()

            def log_message(self, *args):
                pass
        self.http = ThreadingHTTPServer(("127.0.0.1", 0), Files)
        threading.Thread(target=self.http.serve_forever, daemon=True).start()
        profile = self.path.parent / "desktop-browser"
        url = f"http://127.0.0.1:{self.http.server_port}/index.html?edition={self.edition}&serverPort={self.port}&desktopApp=1"
        subprocess.Popen([str(find_browser()), f"--app={url}", f"--user-data-dir={profile}", "--no-first-run",
                          "--window-size=520,850"], creationflags=CREATE_NO_WINDOW)
        self.last_ping = time.monotonic()
        self.game_open = True
        self.root.after(0, self.root.withdraw)

    def watch(self):
        now = time.monotonic()
        if self.game_open and ((self.closed_at and now - self.closed_at > 3) or now - self.last_ping > 120):
            self.close(force=True)
            return
        self.root.after(1500, self.watch)

    def stop_service(self):
        if self.service is not None and self.service.poll() is None:
            self.service.terminate()
            self.service.wait(timeout=8)
        self.service = None
        if self.logfile is not None:
            self.logfile.close()
            self.logfile = None

    def import_file(self):
        if self.busy:
            return
        path = filedialog.askopenfilename(filetypes=[("旅行青蛙存档", "*.frogsave *.json *.jsonl"), ("所有文件", "*.*")])
        if not path:
            return
        if self.path.exists() and not messagebox.askyesno("导入存档", "现有进度会自动备份。导入这份存档吗？"):
            return
        def load():
            if self.game_open:
                raise RuntimeError("请先关闭游戏窗口，再导入存档。")
            import_save(Path(path), self.data_root, self.edition)
        self.run(load)

    def export_file(self):
        if not self.path.exists():
            messagebox.showinfo(self.title, "开始旅程或导入存档后，即可导出进度。")
            return
        path = filedialog.asksaveasfilename(defaultextension=".frogsave", initialfile=f"frog-{self.edition}-{time.strftime('%Y%m%d')}.frogsave",
                                          filetypes=[("旅行青蛙存档", "*.frogsave")])
        if path:
            self.run(lambda: export_save(self.path, Path(path), self.edition))

    def open_folder(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        os.startfile(self.path.parent)

    def close(self, force=False):
        if self.busy:
            return
        if self.game_open and not force:
            self.root.withdraw()
            return
        self.game_open = False
        self.stop_service()
        if self.http is not None:
            self.http.shutdown()
            self.http.server_close()
        self.root.destroy()


def main(edition):
    parser = argparse.ArgumentParser(description="旅行青蛙电脑版")
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--manage", action="store_true", help="打开存档管理")
    args = parser.parse_args()
    root = tk.Tk()
    game = DesktopGame(root, edition, args.data_root)
    if game.path.exists() and not args.manage:
        root.after(100, game.play)
    root.mainloop()
