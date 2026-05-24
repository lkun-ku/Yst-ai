"""`scripts/doc_to_md.py` 的测试（不联网、不依赖真实 .doc）。

这个脚本是**启发式**抽取器（`.doc` 正文在 WordDocument 流里是 UTF-16LE），
所以它的边界必须被钉住：**该丢的噪声要丢，该留的正文要留**。
否则产物会悄悄掺进二进制垃圾，而它看起来"像"大纲。
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.doc_to_md import _is_junk, _is_keep, extract_text, to_markdown  # noqa: E402


def _write_doc_like(path: Path, text: str, *, lead: bytes = b"") -> Path:
    """造一个"像 .doc"的文件：前缀二进制 + UTF-16LE 正文。

    真实 `.doc` 是 OLE 复合文件，本机没有解析库可用；这里只要**能验证抽取逻辑**即可 ——
    脚本对拿不到 `WordDocument` 流的情况本就退化为整文件扫描，正是这条路径。
    """
    path.write_bytes(lead + text.encode("utf-16-le"))
    return path


def test_保留汉字与全角标点丢弃控制字符():
    assert _is_keep("考")
    assert _is_keep("，")      # 全角标点
    assert _is_keep("、")
    assert _is_keep("《")
    assert _is_keep("A")
    assert not _is_keep("\x00")
    assert not _is_keep("\x0b")


def test_单字重复的极短串判为噪声():
    """OLE 头部按 UTF-16LE 解码会产出「橢橢」这类串 —— 真实正文不会长这样。"""
    assert _is_junk("橢橢")
    assert _is_junk("章")
    assert not _is_junk("第一章 考试目标")
    assert not _is_junk("考试目标")


def test_抽取正文并丢弃二进制噪声(tmp_path):
    src = _write_doc_like(
        tmp_path / "101.doc",
        "《综合素质》（幼儿园）\r一、考试目标\r主要考查申请教师资格人员的知识。\r",
        lead=b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + bytes(range(32)),
    )
    out = extract_text(src)
    assert "《综合素质》（幼儿园）" in out
    assert "一、考试目标" in out
    assert "主要考查申请教师资格人员的知识。" in out


def test_纯ascii段被丢弃_它通常是二进制噪声(tmp_path):
    src = _write_doc_like(tmp_path / "x.doc", "abcdefghij\r有效的正文一行\r")
    out = extract_text(src)
    assert "有效的正文一行" in out
    assert "abcdefghij" not in out


def test_连续重复段只保留一次(tmp_path):
    """`.doc` 的快速保存会留重复块 —— 重复会让下游切片数虚高。"""
    src = _write_doc_like(tmp_path / "x.doc", "同一段落\r同一段落\r同一段落\r")
    assert extract_text(src).count("同一段落") == 1


def test_markdown_带frontmatter与权威级别(tmp_path):
    src = _write_doc_like(tmp_path / "101.doc", "《综合素质》（幼儿园）\r")
    md = to_markdown(src, {"title": "101-《综合素质》（幼儿园）笔试大纲", "url": "https://example/101.doc"})
    assert md.startswith("---")
    assert "authority: 官方" in md
    assert "https://example/101.doc" in md
    assert "需人工核对" in md, "产物必须自带「需人工核对」的提醒"


def test_缺元数据时不编造来源(tmp_path):
    """没有来源就写"(未记录)" —— 不许凭文件名或其他线索"补"一个来源上去。"""
    src = _write_doc_like(tmp_path / "abc.doc", "正文一行\r")
    md = to_markdown(src, {})
    assert "(未记录)" in md
