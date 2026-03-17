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


# ---------------- 工单 20 / W-6：逐题精确溯源 ----------------

def _picked():
    return [
        {"id": 12, "seq": 0, "heading_path": "一", "content": "内容A"},
        {"id": 35, "seq": 1, "heading_path": "二", "content": "内容B"},
    ]


def _payload(stem, source_id=None):
    p = {
        "module": "个人资料",
        "knowledge_point": "考点",
        "stem": stem,
        "explanation": "解析",
        "type": "single",
        "options": [{"key": "A", "text": "甲"}, {"key": "B", "text": "乙"}],
        "answer": ["A"],
    }
    if source_id is not None:
        p["source_id"] = source_id
    return p


def _persist(db_session, payloads, picked):
    from app.services.doc_generate import (
        _persist_questions,
        build_source_chunk,
        picked_ids_of,
    )

    return _persist_questions(
        db_session,
        1,
        None,
        payloads,
        set(),
        source_chunk=build_source_chunk(picked),
        picked_ids=picked_ids_of(picked),
    )


def test_picked_ids_of提取合法id集合():
    from app.services.doc_generate import picked_ids_of

    assert picked_ids_of(_picked()) == {12, 35}
    assert picked_ids_of([]) == set()
    assert picked_ids_of(None) == set()


def test_逐题溯源_模型回传合法source_id则精确定位(db_session):
    """模型回传的 source_id 落在召回集合内 → 该题溯源精确到单个切片。"""
    picked = _picked()
    created = _persist(db_session, [_payload("题1", source_id=35)], picked)
    assert len(created) == 1
    assert json.loads(created[0].source_chunk) == [35], "应逐题精确溯源到切片 35"


def test_逐题溯源_source_id越界则回退批级(db_session):
    """模型幻觉出不存在的编号 → 回退到批级 id 清单，不得存非法值。"""
    picked = _picked()
    created = _persist(db_session, [_payload("题1", source_id=999)], picked)
    assert json.loads(created[0].source_chunk) == [12, 35], "越界应回退批级清单"


def test_逐题溯源_source_id非法则回退批级(db_session):
    """source_id 非整数（如字符串/None）→ 回退批级。"""
    picked = _picked()
    created = _persist(db_session, [_payload("题1", source_id="abc")], picked)
    assert json.loads(created[0].source_chunk) == [12, 35]


def test_逐题溯源_不同题可指向不同切片(db_session):
    """核心收益：同批多题可各自指向自己依据的切片（不再是全指向首片）。"""
    picked = _picked()
    created = _persist(
        db_session,
        [_payload("题1", source_id=12), _payload("题2", source_id=35)],
        picked,
    )
    assert len(created) == 2
    assert json.loads(created[0].source_chunk) == [12]
    assert json.loads(created[1].source_chunk) == [35]
