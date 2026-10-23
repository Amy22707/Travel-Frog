"""Windows launcher for both offline editions and their portable saves."""

from __future__ import annotations

import json
import argparse
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from desktop.saves import EDITIONS, create_save, export_save, import_save, save_path

FROZEN = bool(getattr(sys, "frozen", False))
ROOT = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parents[1]
PACKAGES = {"normal": "org.travelfrog.offline.normal", "gallery": "org.travelfrog.offline.gallery"}
DEFAULT_PORTS = {"normal": 52352, "gallery": 52353}
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def listening(port):
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=0.3):
            return True
    except OSError:
        return False


def find_adb():
    candidates = [ROOT / "platform-tools" / "adb.exe", Path(r"D:\leidian\LDPlayer14\adb.exe")]
    found = shutil.which("adb")
    if found:
        candidates.insert(0, Path(found))
    for disk in ("C:", "D:", "E:"):
        for name in ("LDPlayer9", "LDPlayer14"):
            candidates.append(Path(disk + "/") / "leidian" / name / "adb.exe")
    return next((str(p) for p in candidates if p.is_file()), "")


def find_browser():
    for base in (os.environ.get("PROGRAMFILES(X86)", ""), os.environ.get("PROGRAMFILES", ""), os.environ.get("LOCALAPPDATA", "")):
        for relative in ("Microsoft/Edge/Application/msedge.exe", "Google/Chrome/Application/chrome.exe"):
            path = Path(base) / relative
            if path.is_file():
                return path
    raise FileNotFoundError("未找到 Microsoft Edge 或 Google Chrome。请安装其中一个浏览器以运行电脑游戏窗口。")


class GameFiles(SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        pass


class Launcher:
    def __init__(self, root):
        self.root = root
        self.processes = {}
        self.logs = {}
        self.http_server = None
        self.game_windows = {}
        self.config_path = ROOT / "launcher-settings.json"
        try:
            config = json.loads(self.config_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            config = {}
        configured_root = Path(config.get("data_root", "user-data"))
        self.data_root = configured_root if configured_root.is_absolute() else ROOT / configured_root
        self.edition = tk.StringVar(value="normal")
        self.adb = tk.StringVar(value=config.get("adb", find_adb()))
        self.device = tk.StringVar()
        self.name = tk.StringVar(value="呱呱")
        self.port = tk.StringVar(value=str(config.get("ports", DEFAULT_PORTS).get("normal", 52352)))
        self.ports = dict(DEFAULT_PORTS, **config.get("ports", {}))
        self.status = tk.StringVar()
        self.busy = False
        root.title("旅行青蛙 · 离线启动器")
        if (ROOT / "icon.png").is_file():
            self.icon = tk.PhotoImage(file=str(ROOT / "icon.png"))
            root.iconphoto(True, self.icon)
        root.geometry("820x650")
        root.minsize(760, 600)
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TButton", padding=(12, 8))
        style.configure("TLabel", font=("Microsoft YaHei UI", 10))
        panel = ttk.Frame(root, padding=22)
        panel.pack(fill="both", expand=True)
        ttk.Label(panel, text="旅行青蛙 · 离线启动器", font=("Microsoft YaHei UI", 20, "bold")).pack(anchor="w")
        row = ttk.Frame(panel)
        row.pack(fill="x", pady=(18, 10))
        for key, label in EDITIONS.items():
            ttk.Radiobutton(row, text=label, variable=self.edition, value=key, command=self.select_edition).pack(side="left", padx=(0, 22))
        ttk.Label(row, text="服务端口").pack(side="left", padx=(30, 8))
        ttk.Entry(row, textvariable=self.port, width=9).pack(side="left")
        ttk.Label(panel, textvariable=self.status, wraplength=760).pack(anchor="w", pady=(0, 10))
        buttons = ttk.Frame(panel)
        buttons.pack(fill="x")
        for text, fn in [("电脑上玩", self.play_desktop), ("启动服务", self.start), ("停止服务", self.stop)]:
            ttk.Button(buttons, text=text, command=lambda f=fn: self.run(f)).pack(side="left", padx=(0, 10))
        saves = ttk.LabelFrame(panel, text="存档", padding=12)
        saves.pack(fill="x", pady=15)
        r = ttk.Frame(saves)
        r.pack(fill="x")
        ttk.Label(r, text="新青蛙名字").pack(side="left")
        ttk.Entry(r, textvariable=self.name, width=15).pack(side="left", padx=10)
        ttk.Button(r, text="创建初始存档", command=self.create).pack(side="left")
        for text, fn in [("导入存档", self.import_file), ("导出存档", self.export_file), ("打开存档目录", self.open_data)]:
            ttk.Button(saves, text=text, command=fn).pack(side="left", padx=(0, 8), pady=(10, 0))
        devices = ttk.LabelFrame(panel, text="安卓模拟器或 USB 调试设备", padding=12)
        devices.pack(fill="x")
        r = ttk.Frame(devices)
        r.pack(fill="x")
        ttk.Entry(r, textvariable=self.adb).pack(side="left", fill="x", expand=True)
        ttk.Button(r, text="选择 ADB", command=self.choose_adb).pack(side="left", padx=(8, 0))
        r = ttk.Frame(devices)
        r.pack(fill="x", pady=(8, 0))
        self.device_box = ttk.Combobox(r, textvariable=self.device, state="readonly", width=25)
        self.device_box.pack(side="left", padx=(0, 8))
        ttk.Button(r, text="刷新设备", command=lambda: self.run(self.refresh_devices)).pack(side="left", padx=(0, 8))
        ttk.Button(r, text="安装本版本 APK", command=lambda: self.run(self.install)).pack(side="left")
        ttk.Button(r, text="模拟器中玩", command=lambda: self.run(self.play)).pack(side="left", padx=(8, 0))
        self.output = tk.Text(panel, height=7, state="disabled", font=("Consolas", 10), wrap="word")
        self.output.pack(fill="both", expand=True, pady=(15, 0))
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.tick()

    def selected(self):
        return self.edition.get()

    def select_edition(self):
        self.port.set(str(self.ports[self.selected()]))
        self.update_status()

    def log(self, text):
        def append():
            self.output.configure(state="normal")
            self.output.insert("end", text.rstrip() + "\n")
            self.output.see("end")
            self.output.configure(state="disabled")
        self.root.after(0, append)

    def run(self, fn):
        if self.busy:
            return
        self.busy = True
        def work():
            try:
                fn()
            except Exception as exc:
                text = str(exc)
                self.log(text)
                self.root.after(0, lambda: messagebox.showerror("操作未完成", text))
            finally:
                self.busy = False
                self.root.after(0, self.update_status)
        threading.Thread(target=work, daemon=True).start()

    def update_status(self):
        edition = self.selected()
        proc = self.processes.get(edition)
        running = proc is not None and proc.poll() is None
        path = save_path(self.data_root, edition)
        details = "尚未创建或导入存档"
        if path.exists():
            try:
                state = json.loads(path.read_text(encoding="utf-8"))
                role = next(p["data"] for p in state["sync_pushes"] if p["cmd"] == "client.load_role")
                details = f"青蛙：{role['frog']['name']}　已有存档"
            except (ValueError, KeyError, StopIteration):
                details = "存档需要检查"
        self.status.set(f"{EDITIONS[edition]} · {'服务运行中' if running else '服务已停止'} · {details}\n{path}")

    def persist_settings(self):
        try:
            data_root = str(self.data_root.relative_to(ROOT))
        except ValueError:
            data_root = str(self.data_root)
        self.config_path.write_text(json.dumps({"adb": self.adb.get(), "ports": self.ports,
                                               "data_root": data_root}, ensure_ascii=False, indent=2), encoding="utf-8")

    def start(self):
        edition = self.selected()
        proc = self.processes.get(edition)
        if proc is not None and proc.poll() is None:
            self.log("本版本服务已经运行。")
            return
        port = int(self.port.get())
        if not 1024 <= port <= 65535:
            raise ValueError("端口应在 1024 到 65535 之间。")
        if listening(port):
            raise RuntimeError(f"端口 {port} 已被其他服务占用，请关闭原服务或选择其他端口。")
        state = save_path(self.data_root, edition)
        if not state.exists():
            raise RuntimeError("请先创建初始存档或导入自己的存档。")
        command = [str(ROOT / "FrogServer.exe")] if FROZEN else [sys.executable, "-m", "desktop.server_entry"]
        command += ["--data-dir", str(state.parent), "--port", str(port)]
        if edition == "gallery":
            command += ["--gallery"]
        log = (state.parent / "launcher-server.log").open("a", encoding="utf-8")
        try:
            proc = subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=log, creationflags=CREATE_NO_WINDOW)
        except Exception:
            log.close()
            raise
        self.logs[edition] = log
        self.processes[edition] = proc
        self.ports[edition] = port
        for _ in range(60):
            if proc.poll() is not None:
                raise RuntimeError(f"服务启动失败，请查看 {state.parent / 'launcher-server.log'}")
            if listening(port):
                self.persist_settings()
                self.log(f"{EDITIONS[edition]}服务已启动，端口 {port}。")
                return
            time.sleep(0.15)
        self.stop()
        raise RuntimeError("服务启动超时，请查看存档目录中的日志。")

    def stop_edition(self, edition):
        proc = self.processes.pop(edition, None)
        if proc is not None and proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=8)
        stream = self.logs.pop(edition, None)
        if stream is not None:
            stream.close()

    def stop(self):
        self.stop_edition(self.selected())
        self.log("本版本服务已停止。")

    def require_stopped(self):
        proc = self.processes.get(self.selected())
        if self.busy or (proc is not None and proc.poll() is None) or listening(self.ports[self.selected()]):
            raise RuntimeError("请先停止本版本服务器，再修改存档。")

    def create(self):
        try:
            self.require_stopped()
        except Exception as exc:
            messagebox.showerror("无法创建", str(exc))
            return
        edition, name = self.selected(), self.name.get()
        self.run(lambda: self.log(f"已创建存档：{create_save(self.data_root, edition, name)}"))

    def import_file(self):
        try:
            self.require_stopped()
        except Exception as exc:
            messagebox.showerror("无法导入", str(exc))
            return
        path = filedialog.askopenfilename(filetypes=[("离线存档或完整抓包", "*.frogsave *.json *.jsonl"), ("所有文件", "*.*")])
        if not path:
            return
        edition = self.selected()
        if save_path(self.data_root, edition).exists() and not messagebox.askyesno("导入存档", "导入将替换当前存档，并自动备份原存档。是否继续？"):
            return
        self.run(lambda: self.log(f"已导入：{import_save(Path(path), self.data_root, edition)}"))

    def export_file(self):
        edition = self.selected()
        path = filedialog.asksaveasfilename(defaultextension=".frogsave", initialfile=f"frog-{edition}-{time.strftime('%Y%m%d')}.frogsave",
                                          filetypes=[("旅行青蛙存档", "*.frogsave")])
        if path:
            def export():
                export_save(save_path(self.data_root, edition), Path(path), edition)
                self.log(f"已导出：{path}")
            self.run(export)

    def open_data(self):
        path = save_path(self.data_root, self.selected()).parent
        path.mkdir(parents=True, exist_ok=True)
        os.startfile(path)

    def choose_adb(self):
        path = filedialog.askopenfilename(filetypes=[("ADB", "adb.exe"), ("可执行文件", "*.exe")])
        if path:
            self.adb.set(path)
            self.persist_settings()

    def adb_call(self, *args, device=True, timeout=60):
        path = self.adb.get()
        if not path or not Path(path).is_file():
            raise RuntimeError("请选择模拟器自带的 adb.exe，或使用发布包中的 platform-tools。")
        command = [path]
        if device:
            serial = self.device.get()
            if not serial:
                raise RuntimeError("请刷新设备并选择一个已连接的设备。")
            command += ["-s", serial]
        result = subprocess.run(command + list(args), capture_output=True, text=True, encoding="utf-8", errors="replace",
                                timeout=timeout, creationflags=CREATE_NO_WINDOW)
        if result.returncode:
            raise RuntimeError((result.stderr or result.stdout).strip())
        return result.stdout.strip()

    def refresh_devices(self):
        output = self.adb_call("devices", device=False)
        serials = [line.split()[0] for line in output.splitlines() if len(line.split()) == 2 and line.split()[1] == "device"]
        def update():
            self.device_box["values"] = serials
            self.device.set(serials[0] if serials else "")
        self.root.after(0, update)
        self.log("已连接：" + ("、".join(serials) or "无设备；请先启动模拟器并开启 ADB 调试。"))

    def install(self):
        filename = "FrogGallery.apk" if self.selected() == "gallery" else "FrogNormal.apk"
        path = ROOT / "apks" / filename
        if not path.exists():
            raise FileNotFoundError(f"缺少 {path}，请先构建或下载对应的本机安装包。")
        self.log(self.adb_call("install", "-r", str(path), timeout=240))

    def play(self):
        edition = self.selected()
        self.start()
        self.adb_call("reverse", f"tcp:{8081 if edition == 'gallery' else 8080}", f"tcp:{self.ports[edition]}")
        self.adb_call("shell", "am", "start", "-n", f"{PACKAGES[edition]}/org.travelfrog.offline.MainActivity")
        self.log("游戏已启动。")

    def play_desktop(self):
        edition = self.selected()
        browser = find_browser()
        directory = ROOT / "web" / "game" if FROZEN else ROOT / "release" / "TravelFrog" / "web" / "game"
        if not (directory / "index.html").is_file():
            raise FileNotFoundError("缺少电脑版游戏资源，请使用包含 web 文件夹的完整发布目录。")
        self.start()
        if self.http_server is None:
            self.http_server = ThreadingHTTPServer(("127.0.0.1", 0), partial(GameFiles, directory=str(directory)))
            threading.Thread(target=self.http_server.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{self.http_server.server_port}/index.html?edition={edition}&serverPort={self.ports[edition]}"
        profile = self.data_root / edition / "desktop-browser"
        self.game_windows[edition] = subprocess.Popen([str(browser), f"--app={url}", f"--user-data-dir={profile}",
                                                       "--no-first-run", "--window-size=520,850"], creationflags=CREATE_NO_WINDOW)
        self.log(f"{EDITIONS[edition]}电脑游戏窗口已打开。")

    def tick(self):
        if not self.busy:
            self.update_status()
        self.root.after(1500, self.tick)

    def close(self):
        if self.busy:
            messagebox.showinfo("正在操作", "请等待当前操作完成后关闭。")
            return
        for edition in list(self.processes):
            self.stop_edition(edition)
        if self.http_server is not None:
            self.http_server.shutdown()
            self.http_server.server_close()
        self.persist_settings()
        self.root.destroy()


def main():
    parser = argparse.ArgumentParser(description="旅行青蛙离线启动器")
    parser.add_argument("--edition", choices=EDITIONS, default="normal")
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--play", action="store_true", help="打开启动器后直接进入电脑版游戏")
    args = parser.parse_args()
    root = tk.Tk()
    launcher = Launcher(root)
    if args.data_root is not None:
        launcher.data_root = args.data_root.resolve()
    launcher.edition.set(args.edition)
    launcher.select_edition()
    if args.play:
        root.after(100, lambda: launcher.run(launcher.play_desktop))
    root.mainloop()


if __name__ == "__main__":
    main()
