"""工单 17：source_chunk 批级溯源单测。

原实现 `source_chunk=picked[0]["content"]` —— 同批所有题共享首个切片内容，
导致溯源可能指向错误切片。改为记录该批**全部召回切片的 id 清单 JSON**。
"""
import json

import pytest

from app.services.doc_generate import build_source_chunk


def test_批级溯源记录全部召回切片id():
    picked = [
        {"id": 12, "seq": 0, "heading_path": "第一章", "content": "内容A"},
        {"id": 35, "seq": 1, "heading_path": "第二章", "content": "内容B"},
        {"id": 88, "seq": 2, "heading_path": "第三章", "content": "内容C"},
    ]
    out = build_source_chunk(picked)
    assert out is not None
    assert json.loads(out) == [12, 35, 88]


def test_无召回时溯源为None():
    assert build_source_chunk([]) is None
    assert build_source_chunk(None) is None


def test_切片缺id时跳过而非报错():
    """缺 id 的切片（如历史数据）应被跳过；全缺则返回 None，不得抛异常。"""
    assert build_source_chunk([{"seq": 0, "content": "无id"}]) is None
    mixed = [{"id": 7, "content": "有"}, {"seq": 1, "content": "无"}]
    assert json.loads(build_source_chunk(mixed)) == [7]


def test_溯源不再是首片文本():
    """回归护栏：source_chunk 不得再是切片正文（避免误导指向首片）。"""
    picked = [
        {"id": 12, "seq": 0, "content": "这是首片内容可能被误指"},
        {"id": 35, "seq": 1, "content": "第二片"},
    ]
    out = build_source_chunk(picked)
    assert out != picked[0]["content"]
    assert isinstance(json.loads(out), list)
