"""Archive the prepared introduction and offline web app for deployment."""

from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    source = ROOT / "release/TravelFrogWeb"
    if not (source / "index.html").exists():
        raise FileNotFoundError("Run prepare_site.py --out release/TravelFrogWeb first.")
    target = ROOT / "release/TravelFrogWeb.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=3) as archive:
        for file in source.rglob("*"):
            if file.is_file():
                archive.write(file, file.relative_to(source))
        studio = ROOT / "release/FrogStudioEntry.zip"
        if studio.exists():
            archive.write(studio, "studio-entry.zip")
    print(target, flush=True)


if __name__ == "__main__":
    main()
