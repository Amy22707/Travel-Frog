"""Install a Windows edition from its GitHub release or a local release ZIP."""

import os
from pathlib import Path
import shutil
import sys
from tempfile import TemporaryDirectory
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from urllib.request import urlretrieve
import zipfile


RELEASE = "https://github.com/Amy22707/Travel-Frog/releases/latest/download"
EDITIONS = {
    "普通版": ("TravelFrogNormal", "FrogNormal.exe"),
    "图鉴版": ("TravelFrogGallery", "FrogGallery.exe"),
}


def install(edition, destination, download_dir):
    folder, launcher = EDITIONS[edition]
    filename = f"{folder}-Windows.zip"
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / folder
    with TemporaryDirectory(dir=destination) as work:
        work = Path(work)
        archive = download_dir / filename
        if not archive.is_file():
            archive = work / filename
            urlretrieve(f"{RELEASE}/{filename}", archive)
        with zipfile.ZipFile(archive) as package:
            members = package.namelist()
            allowed = {launcher, "FrogServer.exe", "icon.png", "使用说明.md", "存档管理.cmd", "_internal", "web"}
            for member in members:
                parts = Path(member).parts
                if (len(parts) < 2 or parts[0] != folder or parts[1] not in allowed
                        or ".." in parts):
                    raise ValueError(f"安装包文件路径无效：{member}")
            if f"{folder}/{launcher}" not in members or f"{folder}/FrogServer.exe" not in members:
                raise ValueError("安装包不完整。")
            package.extractall(work)
        if target.exists():
            # Only the release files are copied. Saves and import backups stay in user-data.
            shutil.copytree(work / folder, target, dirs_exist_ok=True)
        else:
            (work / folder).rename(target)
    return target / launcher


def main():
    root = tk.Tk()
    root.title("旅行青蛙·中国之旅")
    root.resizable(False, False)
    panel = ttk.Frame(root, padding=24)
    panel.grid()
    edition = tk.StringVar(value="普通版")
    source = Path(sys.executable if getattr(sys, "frozen", False) else __file__).parent
    destination = Path(os.environ["LOCALAPPDATA"]) / "TravelFrog"
    ttk.Label(panel, text="选择要安装的版本").grid(sticky="w")
    choices = [ttk.Radiobutton(panel, text=label, variable=edition, value=label) for label in EDITIONS]
    for choice in choices:
        choice.grid(sticky="w", pady=4)
    status = ttk.Label(panel, text=str(destination))
    status.grid(sticky="w", pady=(12, 4))
    progress = ttk.Progressbar(panel, mode="indeterminate")
    progress.grid(sticky="ew")

    def finish(launcher=None, error=None):
        progress.stop()
        root.protocol("WM_DELETE_WINDOW", root.destroy)
        if error:
            status.configure(text="安装未完成")
            messagebox.showerror("安装未完成", str(error), parent=root)
            for widget in [button, *choices]:
                widget.state(["!disabled"])
            return
        status.configure(text="安装完成")
        button.configure(text="打开游戏", command=lambda: os.startfile(launcher))
        button.state(["!disabled"])

    def start():
        selected = edition.get()
        for widget in [button, *choices]:
            widget.state(["disabled"])
        root.protocol("WM_DELETE_WINDOW", lambda: None)
        status.configure(text="正在安装……")
        progress.start()

        def run():
            try:
                launcher = install(selected, destination, source)
            except Exception as error:
                root.after(0, finish, None, error)
            else:
                root.after(0, finish, launcher)

        threading.Thread(target=run).start()

    button = ttk.Button(panel, text="安装", command=start)
    button.grid(sticky="e", pady=(12, 0))
    root.mainloop()


if __name__ == "__main__":
    main()
