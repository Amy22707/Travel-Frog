"""Download the pinned browser Python runtime into the local build directory."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.29.5"
FILES = ("pyodide.js", "pyodide.asm.js", "pyodide.asm.wasm", "python_stdlib.zip", "pyodide-lock.json")


def main():
    folder = ROOT / ".build" / "pyodide"
    folder.mkdir(parents=True, exist_ok=True)
    def download(name):
        target = folder / name
        if not target.exists():
            with urllib.request.urlopen(f"https://cdn.jsdelivr.net/pyodide/v{VERSION}/full/{name}", timeout=90) as response:
                temporary = target.with_suffix(target.suffix + ".part")
                with temporary.open("wb") as output:
                    while chunk := response.read(256 * 1024):
                        output.write(chunk)
                temporary.replace(target)
        print(f"{name}: {target.stat().st_size}", flush=True)
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(download, FILES))


if __name__ == "__main__":
    main()
