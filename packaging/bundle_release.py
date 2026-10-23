"""Archive the Windows runtime without user saves or local settings."""

from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    for name,exe in (("TravelFrogNormal","FrogNormal.exe"),("TravelFrogGallery","FrogGallery.exe")):
        source = ROOT / "release" / name
        destination = ROOT / "release" / (name+"-Windows.zip")
        allowed = {exe, "FrogServer.exe", "icon.png", "使用说明.md", "存档管理.cmd", "_internal", "web"}
        temporary = destination.with_suffix(".zip.tmp")
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED, compresslevel=3) as archive:
            for path in source.rglob("*"):
                relative = path.relative_to(source)
                if path.is_file() and relative.parts[0] in allowed:
                    archive.write(path, Path(name) / relative)
        temporary.replace(destination)
        print(f"{destination} ({destination.stat().st_size / 1048576:.1f} MiB)",flush=True)


if __name__ == "__main__":
    main()
