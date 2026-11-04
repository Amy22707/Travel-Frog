"""Build the standalone Windows installer after building the game archives."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".build-deps"))


if __name__ == "__main__":
    from PyInstaller.__main__ import run

    run(["--noconfirm", "--onefile", "--windowed", "--name", "TravelFrogSetup",
         "--icon", str(ROOT / ".build/icon.ico"), "--distpath", str(ROOT / "release"),
         "--workpath", str(ROOT / ".build/windows-setup"), "--specpath", str(ROOT / ".build"),
         str(ROOT / "packaging/windows_setup.py")])
