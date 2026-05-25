"""把官方 `.doc`（Word 97-2003 / OLE2）抽成 Markdown —— **启发式，产物需人工核对**。

## 为什么不直接用现成库

官方大纲是以 `.doc` 附件发布的（网页只是下载入口）。本机**没有 antiword / catdoc /
LibreOffice**，而 `.doc` 不是 `.docx`，`python-docx` 读不了（它只认 zip 包装的 OOXML）。

## 启发式原理

`.doc` 的正文文字在 `WordDocument` 流里以 **UTF-16LE** 存放（段落以 `\\r` 分隔）。
于是：按 UTF-16LE 解码 → 只保留「CJK / 全角标点 / ASCII 可打印」→ 合并连续段。

## ⚠️ 两道防线（都是踩出来的）

**① 多编码择优**：并非所有 `.doc` 都是整齐的 UTF-16LE —— 实测有的文件按 UTF-16LE 解码会
产出**海量极短段**（二进制被当成文字）。所以同时试 `utf-16-le` 与 `gbk`，按
「平均段长 − 段数惩罚」打分取优。

**② 质量闸门**：即便择优后仍可能整篇是垃圾（实测有一份抽出 **43915 段**，正常是 200~400）。
这种产物**绝不写盘** —— 宁可标记「转换失败」让人重取，也不让 4 万行噪声进仓库：
它看起来"像"大纲，比缺文件更危险。

## 用法

    python scripts/doc_to_md.py <输入目录> <输出目录>

输入目录下的 `*.doc` 会被转换；输出为同名 `.md`，并附 frontmatter（来源/权威级别/抓取时间）。
可选的 `sources.json`（`{"101": {"title": ..., "url": ...}}`）用于填 frontmatter。

**退出码**：0 = 全部成功；1 = 有无输入或**有转换失败**（失败清单会打印出来）。
"""

from __future__ import annotations

import json
import re
import sys
from datetime import date
from pathlib import Path

#: 保留字符：**常用区**汉字（U+4E00–U+9FFF）、CJK 标点、全角形式、ASCII 可打印。
#:
#: ⚠️ **刻意不含 CJK 扩展 A 区（U+3400–U+4DBF）**：那一片在真实大纲里几乎不出现，
#: 而在这些 `.doc` 的二进制噪声里频繁出现（实测首行出现 `䴠毉`、`䧉踙` 这类串）。
#: 保留它们等于把噪声当正文。代价：正文里若真有扩展区生僻字会丢 —— 对这些文档可接受。
_KEEP_RE = re.compile(r"[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef\x20-\x7e]")

#: 质量闸门阈值：段数与平均段长。正常大纲是 200~400 段、平均段长 20+。
_MAX_SEGMENTS = 1500
_MIN_AVG_LEN = 5.0

#: 备选编码，按"先 UTF-16LE（.doc 的常规）后 GBK（老式 8 位文档）"试
_ENCODINGS = ("utf-16-le", "gbk")


def _is_keep(ch: str) -> bool:
    return bool(_KEEP_RE.match(ch))


def _is_junk(seg: str) -> bool:
    """OLE 头部按 UTF-16LE 解码会产出「橢橢」这类**单字重复的极短串**。

    真实正文不会是「同一个字重复 1~4 次」这种形态，所以这条判据很安全。
    """
    return len(seg) <= 4 and len(set(seg)) == 1


def _segments_from(data: bytes, encoding: str) -> list[str]:
    """某编码下的抽取结果。"""
    text = data.decode(encoding, errors="ignore")
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
    if len(seg) >= 2 and re.search(r"[\u4e00-\u9fff]", seg) and not _is_junk(seg):
        if not lines or lines[-1] != seg:
            lines.append(seg)
    return lines


def score_segments(lines: list[str]) -> float:
    """抽取质量评分：**段越少、段越长越好**（噪声的特征正是"大量极短段"）。

    段数惩罚系数 0.01 是量级取舍：正常大纲 200~400 段、平均段长 20+（得分 ≈ 20）；
    噪声版 4 万段、平均段长 2（得分 ≈ 2 − 400 → 负数）。两者不会混淆。
    """
    if not lines:
        return 0.0
    avg = sum(len(x) for x in lines) / len(lines)
    return avg - len(lines) * 0.01


def extract_text(path: Path) -> tuple[list[str], str]:
    """返回 `(段落列表, 说明)`。说明里写清用了哪条路径，便于排查。

    **两条数据来源都试，取优**：只读 `WordDocument` 流噪声更少，但实测有 12 份文件
    只从流里抽不出任何东西（而整文件扫描能出结果）—— 所以流不是"更好"，
    只是"备选之一"。反过来也有文件是流里干净、整文件全是噪声。**都试、按分取优**才稳。
    """
    candidates: list[tuple[str, bytes]] = []
    try:
        import olefile  # 可选依赖：装了才能只读正文流

        if olefile.isOleFile(str(path)):
            ole = olefile.OleFileIO(str(path))
            if ole.exists("WordDocument"):
                candidates.append(("WordDocument 流", ole.openstream("WordDocument").read()))
    except Exception:  # noqa: BLE001 — 拿不到流不是错误，退化即可
        pass
    candidates.append(("整文件", path.read_bytes()))

    best: list[str] = []
    best_how = "未解出"
    for label, data in candidates:
        for enc in _ENCODINGS:
            segs = _segments_from(data, enc)
            if score_segments(segs) > score_segments(best):
                best, best_how = segs, f"{label} / {enc}"
    return best, best_how


def is_usable(lines: list[str]) -> tuple[bool, str]:
    """质量闸门。返回 `(可用, 原因)` —— 不可用时**不要写盘**。"""
    if not lines:
        return False, "一个有效段都没有"
    if len(lines) > _MAX_SEGMENTS:
        return False, f"段数异常（{len(lines)} > {_MAX_SEGMENTS}），疑似二进制被当成文字"
    avg = sum(len(x) for x in lines) / len(lines)
    if avg < _MIN_AVG_LEN:
        return False, f"平均段长异常（{avg:.1f} < {_MIN_AVG_LEN}），疑似噪声"
    return True, ""


def to_markdown(lines: list[str], meta: dict, *, stem: str, how: str) -> str:
    head = [
        "---",
        f"title: {meta.get('title') or stem}",
        f"source: {meta.get('url') or '(未记录)'}",
        "authority: 官方",  # 本脚本只用于官方 .doc；非官方材料不要走这条路
        f"fetched_at: {date.today().isoformat()}",
        f"extract: {how}",
        f"segments: {len(lines)}",
        "note: 由 scripts/doc_to_md.py 从官方 .doc 启发式抽取，**需人工核对**后再决定是否入库",
        "---",
        "",
    ]
    return "\n".join(head) + "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__.split("## 用法")[-1].strip())
        return 2
    src_dir, out_dir = Path(argv[1]), Path(argv[2])
    out_dir.mkdir(parents=True, exist_ok=True)

    meta_file = src_dir / "sources.json"
    sources: dict = json.loads(meta_file.read_text(encoding="utf-8")) if meta_file.exists() else {}

    docs = sorted(src_dir.glob("*.doc"))
    if not docs:
        print(f"（{src_dir} 下没有 .doc）")
        return 1

    failed: list[str] = []
    for d in docs:
        lines, how = extract_text(d)
        ok, why = is_usable(lines)
        if not ok:
            failed.append(f"{d.name}（{why}）")
            print(f"  ✗ {d.name}　转换失败：{why}　[{how}]　—— **不写文件**")
            continue
        out = out_dir / f"{d.stem}.md"
        out.write_text(to_markdown(lines, sources.get(d.stem, {}), stem=d.stem, how=how), encoding="utf-8")
        print(f"  ✓ {d.name} → {out.name}　{len(lines)} 段　[{how}]　首行：{lines[0][:40]}")

    print(f"\n成功 {len(docs) - len(failed)} / {len(docs)}")
    if failed:
        print("转换失败（需换工具或人工核对原文）：")
        for f in failed:
            print("   -", f)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
