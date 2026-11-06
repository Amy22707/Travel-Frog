"""Mount the shipping disk images, run WebKit and verify portable saves."""

import argparse
import json
import platform
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path


def run(*args, **kwargs):
    return subprocess.run([str(arg) for arg in args], check=True, **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    architecture = platform.machine()
    with tempfile.TemporaryDirectory(prefix="frog-macos-qa-") as directory:
        work = Path(directory)
        mount, saves, restored = work / "disk", work / "saves", work / "restored"
        mount.mkdir()
        for edition, label in (("normal", "Normal"), ("gallery", "Gallery")):
            image = args.directory.resolve() / f"TravelFrog{label}-macOS-{architecture}.dmg"
            run("hdiutil", "attach", "-readonly", "-nobrowse", "-mountpoint", mount, image)
            try:
                bundled, = mount.glob("*.app")
                app = work / bundled.name
                run("ditto", bundled, app)
            finally:
                run("hdiutil", "detach", mount)
            run("codesign", "--verify", "--deep", "--strict", app)
            try:
                run(app / "Contents/MacOS/TravelFrog", "--smoke-test", saves, timeout=160)
            except (subprocess.SubprocessError, OSError):
                for log in (saves / edition).glob("*.log"):
                    print(log.name, log.read_text(encoding="utf-8", errors="replace")[-5000:], flush=True)
                raise
            runtime = app / "Contents/Resources/runtime/FrogRuntime"
            backup = work / f"{edition}.frogsave"
            run(runtime, edition, saves, "export", backup)
            run(runtime, edition, restored, "import", backup)
            run(runtime, edition, restored, "import", backup)
            save = restored / edition / ("gallery_state.json" if edition == "gallery" else "state.json")
            with zipfile.ZipFile(backup) as archive:
                assert json.loads(archive.read("state.json")) == json.loads(save.read_text(encoding="utf-8"))
            assert len(list((save.parent / "import-backups").glob("*.frogsave"))) == 1
            other = "normal" if edition == "gallery" else "gallery"
            assert subprocess.run([runtime, other, restored, "import", backup]).returncode != 0
            # The previous game has exited and its save lock is available for the next launch.
            run(runtime, edition, saves, "import", backup)
            shutil.rmtree(app)
            print(f"{edition}: DMG, startup, shutdown, save export/import and edition separation passed", flush=True)


if __name__ == "__main__":
    main()
