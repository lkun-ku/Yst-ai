"""批改依据的**来源**与**题型过滤** —— 钉住两条容易静默出错的规则。

1. **依据要能取到答案库的采分点**：它此前只查官方语料（考纲/法条），于是模型拿到的是一段
   **描述性文字**，而不是"这份答案的采分点在哪几点"；而答案库里恰有 87 份满分答卷、
   527 条教辅采分点。这是"新语料建好了却没接进链路"的典型 —— 看起来一切正常。
2. **必须限定同题型**：答案库里还有 254 道**单选题**，不筛题型就会把客观题的答案
   当成主观题的采分点，**而且它看起来完全正常**。

两条都属"安静地错"，所以用测试钉住，而不是靠 prompt 里写一句话。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.services import answer_bank  # noqa: E402
from app.services import kb_retrieval  # noqa: E402
from app.services.marking import (  # noqa: E402
    SUBJECTIVE_TYPES,
    _rubric_block,
    retrieve_rubric,
    split_reference_points,
)


def _chunk(cid: str, source_type: str = "official") -> dict:
    return {
        "id": cid,
        "content": f"内容 {cid}",
        "heading_path": f"官方/x#{cid}",
        "source_type": source_type,
        "authority": "半官方" if source_type == "answer_bank" else "官方",
    }


# ---------------- 依据来源：答案库优先、官方兜底 ----------------


def test_批改依据先取答案库_不足再补官方(monkeypatch):
    """k=3、库里有 1 条 → 应补 2 条官方，且**库在前**。"""
    seen: dict = {}

    def fake_search(query, k=8, types=None):
        seen["query"] = query
        seen["k"] = k
        seen["types"] = types
        return [_chunk("bank-1", "answer_bank")]

    def fake_retrieve(db, query, scope, k=6, embed_fn=None):
        seen["official_k"] = k
        return [_chunk(f"off-{i}") for i in range(k)]

    monkeypatch.setattr(answer_bank, "search_answer_bank", fake_search)
    monkeypatch.setattr(kb_retrieval, "retrieve", fake_retrieve)

    out = retrieve_rubric(object(), object(), "material", k=3)
    assert [c["source_type"] for c in out] == ["answer_bank", "official", "official"]
    assert seen["types"] == {"material"}, "必须**限定同题型**去库里找"
    assert seen["official_k"] == 2, "官方那一路只该补差额，不该重复取满"


def test_库里够数时不再查官方(monkeypatch):
    """库里已够 k 条 → 不该白花一次官方检索（尤其官方那路可能带 embed 调用）。"""
    monkeypatch.setattr(
        answer_bank,
        "search_answer_bank",
        lambda query, k=8, types=None: [_chunk(f"b{i}", "answer_bank") for i in range(k)],
    )

    def boom(*a, **kw):  # pragma: no cover - 被调用即失败
        raise AssertionError("库里够数时不应再查官方语料")

    monkeypatch.setattr(kb_retrieval, "retrieve", boom)
    out = retrieve_rubric(object(), object(), "writing", k=2)
    assert len(out) == 2 and all(c["source_type"] == "answer_bank" for c in out)


def test_题型不在主观集里时不查库只查官方(monkeypatch):
    """`single`（单选）等题型不该去库里找"评分依据" —— 那本来就不是主观题。"""

    def boom(*a, **kw):  # pragma: no cover - 被调用即失败
        raise AssertionError("非主观题型不应查答案库")

    monkeypatch.setattr(answer_bank, "search_answer_bank", boom)
    monkeypatch.setattr(kb_retrieval, "retrieve", lambda db, q, s, k=6, embed_fn=None: _c(k))
    out = retrieve_rubric(object(), object(), "single", k=2)
    assert all(c["source_type"] == "official" for c in out)
    assert "single" not in SUBJECTIVE_TYPES


def test_官方检索失败也不让批改整体失败(monkeypatch):
    """与答题同原则：官方那一路挂了，不该把已有的依据一起丢掉。"""
    monkeypatch.setattr(
        answer_bank, "search_answer_bank",
        lambda query, k=8, types=None: [_chunk("bank-1", "answer_bank")],
    )

    def boom(*a, **kw):
        raise RuntimeError("检索炸了")

    monkeypatch.setattr(kb_retrieval, "retrieve", boom)
    out = retrieve_rubric(object(), object(), "material", k=3)
    assert [c["id"] for c in out] == ["bank-1"]


def _c(k: int) -> list[dict]:
    return [_chunk(f"off-{i}") for i in range(k)]


# ---------------- 题型过滤本身 ----------------


def test_题型过滤挡掉单选题(monkeypatch):
    """不筛题型时**单选题会一起被检索出来** —— 这正是要挡掉的情形。"""
    entries = [
        {"id": "x1", "type": "single", "stem": "评析王老师的教育行为是否正确", "answer": ["A"]},
        {"id": "m1", "type": "material", "stem": "评析王老师的教育行为", "points": ["关爱学生"]},
    ]
    monkeypatch.setattr(answer_bank, "load_entries", lambda: entries)

    only_material = answer_bank.search_answer_bank("评析王老师的教育行为", 8, types={"material"})
    assert [c["id"] for c in only_material] == ["m1"]

    unfiltered = answer_bank.search_answer_bank("评析王老师的教育行为", 8)
    assert "x1" in [c["id"] for c in unfiltered], "不筛题型时单选确实会混进来（所以要筛）"


# ---------------- 权威级别必须随依据一起给模型 ----------------


def test_依据块标出半官方来源():
    bank = [dict(_chunk("b1", "answer_bank"), heading_path="答案库/真题答卷库.json#2024下-30")]
    official = [_chunk("o1")]
    assert "答案库·半官方" in _rubric_block(bank)
    assert "答案库·半官方" not in _rubric_block(official)


def test_没有依据时给出显式占位而不是空串():
    assert _rubric_block([]) == "（没有可引用的依据）"


# ---------------- 数据级回归（防"重建答案库后依据悄悄变空"）----------------


def test_真实答案库对主观题型能产出依据(monkeypatch):
    """答案库经 `scripts/build_answer_bank.py` **重建**后，若字段名或题型取值变了，
    批改的依据会**悄悄变空** —— 而空依据在 `measure_consistency` 里会返回 `n=0`，
    那与"批改很稳"看起来一模一样（`routers/marking.py` 为此专门回 409）。

    ⚠️ 本用例**故意**违反"测试与仓库数据无关"的默认约定（`conftest._isolate_answer_bank`
    autouse 把答案库指到空目录）：它要守的恰恰是**数据与代码之间的契约** ——
    题型名/字段名一旦与构建脚本的产物不一致，依据就会静默变空。所以必须读真实库。
    """
    from app.services import answer_bank as ab

    real = Path(__file__).resolve().parents[1] / "data" / "answer_bank"
    monkeypatch.setattr(ab.settings, "answer_bank_dir", str(real))
    monkeypatch.setattr(ab.settings, "answer_bank_enabled", True)

    for qt in ("material", "writing"):
        hits = retrieve_rubric(None, None, qt, k=3)  # db=None → 只走答案库这一路
        assert hits, f"{qt} 题型取不到任何批改依据（答案库是否被重建坏了？）"
        assert all(h["source_type"] == "answer_bank" for h in hits)


# ---------------- 采分点内容的质量护栏 ----------------


def test_水印碎片不会被当成采分点():
    """实测踩到过「对公众号」这种碎片（OCR 把教辅引流文案切进了参考答案）。

    ⚠️ 但**只挡高精度词**：「关注」这类词在教育语境里是**真实采分点**的常用词
    （「关注学生的个体差异」），挡它会让批改**少给分** —— 那比留下一点噪声更糟。
    """
    text = "对公众号\n首先，素质教育是面向全体学生的教育。\n关注学生的个体差异，因材施教。\n微信扫码领取资料"
    assert split_reference_points(text) == [
        "首先，素质教育是面向全体学生的教育。",
        "关注学生的个体差异，因材施教。",
    ]


def _use_real_bank(monkeypatch) -> None:
    """把答案库指向仓库里的**真实**库（本文件的数据级用例都用它，原因见上面那条用例的说明）。"""
    from app.services import answer_bank as ab

    real = Path(__file__).resolve().parents[1] / "data" / "answer_bank"
    monkeypatch.setattr(ab.settings, "answer_bank_dir", str(real))
    monkeypatch.setattr(ab.settings, "answer_bank_enabled", True)


# ---------------- 评分规则条目（判分口径）----------------


def test_批改依据必带同题型的评分规则(monkeypatch):
    """规则条目很短、关键词得分抢不过上千字的满分答卷，所以它必须被**必带**、且排在**最前**。

    这条钉住的是一个设计决定：**判分口径优先于样例**。少了它，依据里只剩某几年的满分答案，
    而"按什么给分"反而看不到。
    """
    _use_real_bank(monkeypatch)
    for qt in ("material", "writing"):
        ids = [h.get("id") for h in retrieve_rubric(None, None, qt, k=3)]
        assert f"rule-{qt}" in ids, f"{qt} 的评分规则没进依据（实得 {ids}）"
        assert ids[0] == f"rule-{qt}", "规则应排在**最前**（先口径、后样例）"


def test_规则条目也是半官方_不冒充官方(monkeypatch):
    _use_real_bank(monkeypatch)
    rule = [h for h in retrieve_rubric(None, None, "material", k=3)
            if h.get("id") == "rule-material"][0]
    assert rule["authority"] == "半官方"
    assert "答案库·半官方" in _rubric_block([rule])


def test_每种主观题型都有规则条目(monkeypatch):
    """数据级契约：题型与规则条目的对应关系缺一个，就有一类题**没有判分口径**。"""
    from app.services import answer_bank as ab

    _use_real_bank(monkeypatch)
    types = {
        str(e.get("type"))
        for e in ab.load_entries()
        if str(e.get("id") or "").startswith(ab.RULES_ID_PREFIX)
    }
    assert {"material", "writing", "short", "analysis"} <= types, types


def test_规则缺失时官方兜底照常工作(monkeypatch):
    """规则是"必带"，但不是"必须有" —— 某题型还没写规则、库里也没该题型的条目时，
    应当**照常回落官方语料**，而不是返回空、更不该抛异常。"""
    _use_real_bank(monkeypatch)
    monkeypatch.setattr(
        kb_retrieval, "retrieve",
        lambda db, q, s, k=6, embed_fn=None: [_chunk(f"off-{i}") for i in range(k)],
    )
    hits = retrieve_rubric(object(), object(), "design", k=2)
    assert hits and all(h["source_type"] == "official" for h in hits)


def test_写作采分点不含参考范文():
    """范文是**整篇示例**，不是采分点。不切掉它，整篇范文会变成一个长"采分点"，
    而它一旦进了批改依据，模型就会拿一篇范文去给考生的作文找采分点。"""
    from scripts.parse_subjective_ocr import build

    hdr = "2024年下半年&教师资格证考试笔试真题中学&《综合素质》"
    q = (
        f"{hdr}\n===== page 1 =====\n"
        "33.阅读下面的材料，根据要求作文。（50分）\n"
        "要求：自拟标题，自选角度，不少于八百字，文体不限。\n"
    )
    a = (
        f"{hdr}\n===== page 1 =====\n"
        "33．正确答案是：【立意分析】围绕勇于挑战不可能展开，结构清晰，语言流畅。\n"
        "【参考范文】曾经有一位农民在海拔六百米的荒山上种出了优质脆桃，这告诉我们……\n"
    )
    data = build(q, a)
    assert data["stats"]["n_writing_trimmed"] == 1
    joined = "\n".join(data["items"][0]["points"])
    assert "立意分析" in joined
    assert "范文" not in joined, "参考范文不该被当成采分点"
    assert "农民" not in joined, "范文正文（整篇示例）不该进采分点"
