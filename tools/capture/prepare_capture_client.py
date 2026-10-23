"""Enable chunked protocol logging in a locally supplied official client."""

import argparse
from pathlib import Path

CHUNK_LOG = (
    't.pushLog(r),(function(){'
    't._frogLogId=(t._frogLogId||0)+1;'
    'for(var o=0,a=Math.ceil(r.length/240);o<a;o++)'
    'console.log("FROG|"+t._frogLogId+"|"+o+"|"+a+"|"+r.substr(240*o,240))'
    '})()'
)


def patch(source: str) -> str:
    targets = (
        ("core.DebugConfig.PrintLog=e.printLog", "core.DebugConfig.PrintLog=!0", 1),
        ("e.printLog===!1&&this.closeLog()", "!1&&this.closeLog()", 1),
        ("t.pushLog(r),console.log(r)", CHUNK_LOG, 2),
    )
    for old, new, count in targets:
        if source.count(old) == count:
            source = source.replace(old, new)
        elif source.count(old) != 0 or source.count(new) != count:
            raise ValueError("当前客户端版本不匹配；未生成采集脚本。")
    return source


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.input.resolve() == args.output.resolve():
        parser.error("请保留原文件，输出到另一个路径。")
    result = patch(args.input.read_text(encoding="utf-8-sig"))
    args.output.write_text(result, encoding="utf-8", newline="")
    print(f"已生成采集脚本：{args.output}；服务器连接保持不变。")


if __name__ == "__main__":
    main()
