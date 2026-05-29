"""答案库（方案 B）的测试 —— 独立于知识库、优先检索、权威级别必须带。

钉住的四件事：
① 答案库**不在** `official_kb_dir` 下（否则会被 `kb_corpus` 自动摄入，破坏 B 的前提）；
② 答题时**答案库优先**，不足才回落官方语料；
③ 每条命中**必须带 authority=半官方**（禁止冒充官方）；
④ 命中内容要能支撑引用子串校验（防幻觉的硬机制不能绕）。
"""

from __future__ import annotations

import json

import pytest

from app.services import answer_bank as ab
from app.services.answer_bank import (
    _entry_text,
    bank_dir,
    load_entries,
    retrieve_for_question,
    search_answer_bank,
)


@pytest.fixture
def bank(tmp_path, monkeypatch):
    """造一个临时答案库，并指向它。"""
    items = [
        {
            "id": "2025上-6",
            "type": "single",
            "stem": "高中生陈某对学校给予的记过处分不服，依据教育法可以申诉。",
            "options": [
                {"key": "A", "text": "申诉"},
                {"key": "B", "text": "仲裁"},
                {"key": "C", "text": "复议"},
                {"key": "D", "text": "诉讼"},
            ],
            "answer": ["A"],
        },
        {
            "id": "2025上-8",
            "type": "single",
            "stem": "某校变相推销商品，依据义务教育法应由县级人民政府教育行政部门责令改正。",
            "answer": ["D"],
        },
    ]
    f = tmp_path / "bank.json"
    f.write_text(json.dumps({"items": items}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(ab.settings, "answer_bank_enabled", True)
    monkeypatch.setattr(ab.settings, "answer_bank_dir", str(tmp_path))
    return tmp_path


def test_答案库默认目录不在_official_之下(monkeypatch):
    """**这是方案 B 的前提**：一旦落进 `data/official/`，就会被 `kb_corpus` 自动摄入，
    那样真题就混进了官方语料，B 方案也就不存在了。

    ⚠️ 这条起初被我写成了拿 `tmp_path` 比较的断言（毫无意义，必然失败）——
    真正要保证的是**默认目录**的位置，与测试用的临时目录无关。
    """
    monkeypatch.setattr(ab.settings, "answer_bank_enabled", True)
    monkeypatch.setattr(ab.settings, "answer_bank_dir", "")  # 用默认（仓库内 data/answer_bank）
    d = bank_dir()
    assert d is not None, "仓库里应当存在默认答案库目录"
    norm = str(d).replace("\\", "/")
    assert norm.endswith("data/answer_bank")
    assert "/data/official" not in norm, "答案库绝不能落在 official 之下"
    official = str(ab.settings.official_kb_dir or "data/official").replace("\\", "/")
    assert "answer_bank" not in official


def test_检索命中并带权威级别(bank):
    hits = search_answer_bank("记过处分不服可以申诉", k=5)
    assert hits, "应当命中"
    assert hits[0]["id"] == "2025上-6"
    assert all(h["authority"] == "半官方" for h in hits), "**必须**标注半官方，禁止冒充官方"
    assert all(h["source_type"] == "answer_bank" for h in hits)


def test_命中内容可用于引用子串校验(bank):
    """防幻觉的硬机制是 quote 必须 in content —— 答案库条目也得满足。"""
    hits = search_answer_bank("记过处分不服可以申诉", k=1)
    assert "记过处分" in hits[0]["content"]
    assert "申诉" in hits[0]["content"]


def test_无关查询不命中(bank):
    assert search_answer_bank("今天的天气情况如何", k=5) == []


def test_空查询不命中且空库返回空(bank, tmp_path, monkeypatch):
    assert search_answer_bank("", k=5) == []
    monkeypatch.setattr(ab.settings, "answer_bank_dir", str(tmp_path / "不存在"))
    assert load_entries() == []


def test_答案库禁用时不检索(bank, monkeypatch):
    monkeypatch.setattr(ab.settings, "answer_bank_enabled", False)
    assert bank_dir() is None
    assert search_answer_bank("记过处分", k=3) == []


def test_答题检索_答案库优先且不足才回落官方(bank, monkeypatch):
    calls: list[int] = []

    def _fake_retrieve(db, query, scope, k):
        calls.append(k)
        return [{"id": "official-1", "content": "官方切片内容", "heading_path": "法条/教师法"}]

    monkeypatch.setattr("app.services.kb_retrieval.retrieve", _fake_retrieve)

    # ① 答案库已够（k=1，命中 ≥1）→ 不回落
    out = retrieve_for_question(object(), None, "记过处分不服可以申诉", k=1)
    assert len(out) == 1 and out[0]["source_type"] == "answer_bank"
    assert calls == []
    # ② 答案库不够（k=3，命中 1 条）→ 回落，且只补差额
    out = retrieve_for_question(object(), None, "记过处分不服可以申诉", k=3)
    assert [h["source_type"] for h in out] == ["answer_bank", "official"]
    assert calls == [2]  # 只补 2 条，不是重新查 3 条
    assert out[1]["authority"] == "官方"


def test_官方检索失败时仍有答案库结果(bank, monkeypatch):
    def _boom(db, query, scope, k):
        raise RuntimeError("接口挂了")

    monkeypatch.setattr("app.services.kb_retrieval.retrieve", _boom)
    out = retrieve_for_question(object(), None, "记过处分", k=3)
    assert out and all(h["source_type"] == "answer_bank" for h in out)


def test_条目文本包含题干选项答案与采分点():
    e = {
        "stem": "题干内容",
        "options": [{"key": "A", "text": "甲"}, {"key": "B", "text": "乙"}],
        "answer": ["A"],
        "points": ["要点一", "要点二"],
        "reference": "示范作答",
    }
    t = _entry_text(e)
    assert "题干内容" in t and "A.甲" in t and "答案：A" in t
    assert "采分点：要点一" in t and "示范作答" in t
