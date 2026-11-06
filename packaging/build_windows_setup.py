"""Build self-contained Windows installers with Inno Setup 6.7 or later."""

from pathlib import Path
import argparse
import os
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[1]
EDITIONS = {
    "normal": ("TravelFrogNormal", "FrogNormal.exe"),
    "gallery": ("TravelFrogGallery", "FrogGallery.exe"),
}


def compiler_path(explicit=None):
    candidates = [explicit, os.environ.get("FROG_ISCC"), shutil.which("ISCC.exe"),
                  ROOT / ".build/tools/innosetup/ISCC.exe"]
    for variable in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
        base = Path(os.environ.get(variable, "."))
        candidates.extend([base / "Inno Setup 6/ISCC.exe", base / "Inno Setup 7/ISCC.exe",
                           base / "Programs/Inno Setup 6/ISCC.exe"])
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate).resolve()
    raise SystemExit("请安装 Inno Setup 6.7 或更新版本，或用 --compiler 指定 ISCC.exe。")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--edition", choices=["normal", "gallery", "both"], default="both")
    parser.add_argument("--compiler", type=Path)
    parser.add_argument("--version", default="0.2.4")
    args = parser.parse_args()
    compiler = compiler_path(args.compiler)
    editions = EDITIONS if args.edition == "both" else [args.edition]
    for edition in editions:
        folder, launcher = EDITIONS[edition]
        source = ROOT / "release" / folder
        for relative in (launcher, "FrogServer.exe", "icon.png", "存档管理.cmd",
                         "_internal/FrogWindow/FrogWindow.exe", "web/game/index.html"):
            if not (source / relative).is_file():
                raise SystemExit(f"请先构建完整的 Windows 游戏：缺少 {source / relative}")
        shutil.copyfile(ROOT / "packaging/USER-GUIDE.md", source / "使用说明.md")
        subprocess.run([str(compiler), "/Qp", f"/DEdition={edition}", f"/DGameDir={source}",
                        f"/DRootDir={ROOT}", f"/DAppVersion={args.version}",
                        str(ROOT / "packaging/windows.iss")], check=True)
        output = ROOT / "release" / f"{folder}-Setup.exe"
        print(f"{output} ({output.stat().st_size / 1048576:.1f} MiB)", flush=True)


if __name__ == "__main__":
    main()
