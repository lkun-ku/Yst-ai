"""把官方 `.doc`（Word 97-2003 / OLE2）抽成 Markdown —— **启发式，产物需人工核对**。

## 为什么不直接用现成库

官方大纲是以 `.doc` 附件发布的（网页只是下载入口）。本机**没有 antiword / catdoc /
LibreOffice**，而 `.doc` 不是 `.docx`，`python-docx` 读不了（它只认 zip 包装的 OOXML）。

## 启发式原理

`.doc` 的正文文字在 `WordDocument` 流里以 **UTF-16LE** 存放（段落以 `\\r` 分隔）。
于是：按 UTF-16LE 解码 → 只保留「CJK / 全角标点 / ASCII 可打印」→ 合并连续段。
对本场景（**纯文字的大纲**）足够；对含复杂表格、图片、分栏的文档会丢内容。

⚠️ **它不是一个通用 .doc 转换器**，所以：
1. 转换后会打印每份文件的**前若干行**，用来肉眼核对；
2. 正文里的章节标题（`一、考试目标` 之类）就是核对锚点；
3. 核对不通过时**不要**把产物往 `data/official/` 放。

## 用法

    python scripts/doc_to_md.py <输入目录> <输出目录>

输入目录下的 `*.doc` 会被转换；输出为同名 `.md`，并附 frontmatter（来源/权威级别/抓取时间）。
可选的 `sources.json`（`{"101": {"title": ..., "url": ...}}`）用于填 frontmatter。
"""

from __future__ import annotations

import json
import re
import sys
from datetime import date
from pathlib import Path

#: 保留字符：CJK 汉字、CJK 标点、全角形式、ASCII 可打印
_KEEP_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf\u3000-\u303f\uff00-\uffef\x20-\x7e]")


def _is_keep(ch: str) -> bool:
    return bool(_KEEP_RE.match(ch))


def _is_junk(seg: str) -> bool:
    """OLE 头部按 UTF-16LE 解码会产出「橢橢」这类**单字重复的极短串**。

    真实正文不会是「同一个字重复 1~4 次」这种形态，所以这条判据很安全。
    """
    return len(seg) <= 4 and len(set(seg)) == 1


def extract_text(path: Path) -> str:
    """从 `.doc` 抽正文。优先只读 `WordDocument` 流（`olefile` 可用时），否则整文件扫描。"""
    data = path.read_bytes()
    try:
        import olefile  # 可选依赖：没装就退化为整文件扫描（噪声多一些，仍可用）

        if olefile.isOleFile(str(path)):
            ole = olefile.OleFileIO(str(path))
            if ole.exists("WordDocument"):
                data = ole.openstream("WordDocument").read()
    except Exception:  # noqa: BLE001 — 拿不到流不是错误，退化即可
        pass

    text = data.decode("utf-16-le", errors="ignore")

    lines: list[str] = []
    buf: list[str] = []
    for ch in text:
        if _is_keep(ch):
            buf.append(ch)
            continue
        seg = "".join(buf).strip()
        buf = []
        # 至少含一个汉字才认为是有意义的正文段（纯 ASCII 段多为二进制噪声）
        if len(seg) >= 2 and re.search(r"[\u4e00-\u9fff]", seg) and not _is_junk(seg):
            if not lines or lines[-1] != seg:  # 跳过连续重复（.doc 的快速保存会留重复块）
                lines.append(seg)
    seg = "".join(buf).strip()
    if len(seg) >= 2 and re.search(r"[\u4e00-\u9fff]", seg):
        lines.append(seg)
    return "\n".join(lines)


def to_markdown(src: Path, meta: dict) -> str:
    head = [
        "---",
        f"title: {meta.get('title') or src.stem}",
        f"source: {meta.get('url') or '(未记录)'}",
        "authority: 官方",  # 本脚本只用于官方 .doc；非官方材料不要走这条路
        f"fetched_at: {date.today().isoformat()}",
        "note: 由 scripts/doc_to_md.py 从官方 .doc 启发式抽取，**需人工核对**后再决定是否入库",
        "---",
        "",
    ]
    return "\n".join(head) + extract_text(src) + "\n"


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__.split("## 用法")[-1].strip())
        return 2
    src_dir, out_dir = Path(argv[1]), Path(argv[2])
    out_dir.mkdir(parents=True, exist_ok=True)

    meta_file = src_dir / "sources.json"
    sources: dict = {}
    if meta_file.exists():
        sources = json.loads(meta_file.read_text(encoding="utf-8"))

    docs = sorted(src_dir.glob("*.doc"))
    if not docs:
        print(f"（{src_dir} 下没有 .doc）")
        return 1

    for d in docs:
        md = to_markdown(d, sources.get(d.stem, {}))
        out = out_dir / f"{d.stem}.md"
        out.write_text(md, encoding="utf-8")
        body = md.split("---", 2)[-1].strip().splitlines()
        print(f"\n=== {d.name} → {out.name}　正文 {len(body)} 段　（前 6 行供核对）===")
        for ln in body[:6]:
            print("   ", ln[:78])
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
