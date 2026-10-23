"""Command-line access to the same save operations used by the launcher."""

import argparse
from pathlib import Path

from desktop.saves import create_save, export_save, import_save, save_path


def main():
    parser = argparse.ArgumentParser(description="旅行青蛙离线存档管理")
    parser.add_argument("action", choices=["new", "import", "export"])
    parser.add_argument("--edition", choices=["normal", "gallery"], default="normal")
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--file", type=Path)
    parser.add_argument("--name", default="呱呱")
    args = parser.parse_args()
    if args.action == "new":
        print(create_save(args.data_root, args.edition, args.name))
    elif args.file is None:
        parser.error("导入或导出需要 --file")
    elif args.action == "import":
        print(import_save(args.file, args.data_root, args.edition))
    else:
        export_save(save_path(args.data_root, args.edition), args.file, args.edition)
        print(args.file)


if __name__ == "__main__":
    main()
