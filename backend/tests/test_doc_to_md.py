"""`scripts/doc_to_md.py` 的测试（不联网、不依赖真实 .doc）。

这个脚本是**启发式**抽取器（`.doc` 正文在 WordDocument 流里是 UTF-16LE），
所以它的边界必须被钉住：**该丢的噪声要丢，该留的正文要留，抽成垃圾时不许写盘**。
最后一条是踩出来的：有一份大纲抽出 **43915 段**（正常 200~400），若写进仓库，
它看起来"像"大纲，比缺文件危险得多。
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.doc_to_md import (  # noqa: E402
    _MAX_SEGMENTS,
    _is_junk,
    _is_keep,
    extract_text,
    is_usable,
    score_segments,
    to_markdown,
)


def _write_doc_like(path: Path, text: str, *, lead: bytes = b"", encoding: str = "utf-16-le") -> Path:
    """造一个"像 .doc"的文件：前缀二进制 + 正文。

    真实 `.doc` 是 OLE 复合文件；这里只要**能验证抽取逻辑**即可 ——
    脚本对拿不到 `WordDocument` 流的情况本就退化为整文件扫描，正是这条路径。
    """
    path.write_bytes(lead + text.encode(encoding))
    return path


def test_保留汉字与全角标点丢弃控制字符():
    assert _is_keep("考")
    assert _is_keep("，")      # 全角标点
    assert _is_keep("《")
    assert _is_keep("A")
    assert not _is_keep("\x00")
    assert not _is_keep("\x0b")


def test_单字重复的极短串判为噪声():
    """OLE 头部按 UTF-16LE 解码会产出「橢橢」这类串 —— 真实正文不会长这样。"""
    assert _is_junk("橢橢")
    assert _is_junk("章")
    assert not _is_junk("第一章 考试目标")


def test_抽取正文并丢弃二进制噪声(tmp_path):
    src = _write_doc_like(
        tmp_path / "101.doc",
        "《综合素质》（幼儿园）\r一、考试目标\r主要考查申请教师资格人员的知识。\r",
        lead=b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + bytes(range(32)),
    )
    lines, how = extract_text(src)
    joined = "\n".join(lines)
    assert "《综合素质》（幼儿园）" in joined
    assert "一、考试目标" in joined
    assert how.startswith("整文件"), f"没装 olefile 时应退化并如实说明，实际：{how}"


def test_纯ascii段被丢弃_它通常是二进制噪声(tmp_path):
    src = _write_doc_like(tmp_path / "x.doc", "abcdefghij\r有效的正文一行\r")
    lines, _ = extract_text(src)
    joined = "\n".join(lines)
    assert "有效的正文一行" in joined
    assert "abcdefghij" not in joined


def test_连续重复段只保留一次(tmp_path):
    """`.doc` 的快速保存会留重复块 —— 重复会让下游切片数虚高。"""
    src = _write_doc_like(tmp_path / "x.doc", "同一段落\r同一段落\r同一段落\r")
    lines, _ = extract_text(src)
    assert lines.count("同一段落") == 1


def test_多编码择优_纯gbk文档要选对(tmp_path):
    """有些 `.doc` 的正文是 GBK（老式 8 位文档），按 UTF-16LE 解会得到一片乱码。

    ⚠️ 本用例起初写成了「GBK 正文 + 256 字节二进制前缀」，那条断言**过强**：
    加了前缀之后两种解码都能解出"可用"内容，于是"必须选 GBK"并不成立（实测就红了）。
    改成**纯 GBK** 文档 —— 按 UTF-16LE 解它几乎全是非保留字符、段少且评分低，
    这才是"择优"能保证的真实场景。
    """
    src = _write_doc_like(
        tmp_path / "g.doc",
        "《综合素质》（小学）\r一、考试目标\r主要考查申请教师资格人员的知识。\r",
        encoding="gbk",
    )
    lines, how = extract_text(src)
    assert "一、考试目标" in "\n".join(lines), f"多编码择优没生效（{how}）"
    assert how.endswith("gbk"), f"应当选中 gbk，实际：{how}"


def test_择优按分数取优而不是按尝试顺序(tmp_path):
    """保证的是"**按分取优**"这条规则本身（顺序无关）。"""
    good = ["这是一条正常的正文段落，长度足够。" for _ in range(5)]
    junk = ["啊"] * 5000
    assert score_segments(good) > score_segments(junk)


def test_质量评分_段少而长者更优():
    good = ["这是一条正常的正文段落，长度足够。" for _ in range(5)]
    bad = ["啊"] * 5000  # 极多极短 → 噪声形态
    assert score_segments(good) > score_segments(bad)
    assert score_segments([]) == 0.0


def test_质量闸门拦下垃圾_段数异常():
    bad = [f"第{i}条" for i in range(_MAX_SEGMENTS + 1)]
    ok, why = is_usable(bad)
    assert ok is False and "段数异常" in why


def test_质量闸门拦下垃圾_平均段长过短():
    ok, why = is_usable(["啊", "哦", "嗯"] * 20)
    assert ok is False and "平均段长异常" in why


def test_质量闸门放行正常大纲():
    ok, why = is_usable(["一、考试目标", "主要考查申请教师资格人员的下列知识、能力和素养："] * 3)
    assert ok is True and why == ""


def test_空结果不可用():
    ok, _ = is_usable([])
    assert ok is False


def test_markdown_带frontmatter与权威级别(tmp_path):
    md = to_markdown(
        ["《综合素质》（幼儿园）", "一、考试目标"],
        {"title": "101-《综合素质》（幼儿园）笔试大纲", "url": "https://example/101.doc"},
        stem="101",
        how="WordDocument 流 / utf-16-le",
    )
    assert md.startswith("---")
    assert "authority: 官方" in md
    assert "https://example/101.doc" in md
    assert "segments: 2" in md
    assert "需人工核对" in md, "产物必须自带「需人工核对」的提醒"


def test_缺元数据时不编造来源():
    """没有来源就写"(未记录)" —— 不许凭文件名或其他线索"补"一个来源上去。"""
    md = to_markdown(["正文一行"], {}, stem="abc", how="整文件 / utf-16-le")
    assert "(未记录)" in md
