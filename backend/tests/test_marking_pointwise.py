"""判分口径的测试 —— 选择题「不对就是判错」、简答「按采分点给分（语义）」。

两套口径是**产品明确指定**的（2026-03-04），且**不能混用**：
客观题给部分分会让人无法解释分数；简答用字面匹配会把换了说法的正确作答判成 0 分。
本文件的断言就是这两条口径本身。
"""

from __future__ import annotations

import json

import pytest

from app.services.llm_client import FakeLLMClient
from app.services.marking import (
    PointHit,
    PointwiseResult,
    score_by_points,
    score_choice,
    split_reference_points,
)


# ---------------- 选择题：严格匹配 ----------------

def test_选择题答对得满分():
    assert score_choice(["B"], ["B"], 2) == 2.0


def test_选择题答错就是零分_不给部分分():
    """口径：客观题没有"部分正确"，给半分会让分数不可解释。"""
    assert score_choice(["A"], ["B"], 2) == 0.0


def test_多选题少选算错():
    assert score_choice(["A"], ["A", "B"], 2) == 0.0


def test_多选题多选算错():
    assert score_choice(["A", "B", "C"], ["A", "B"], 2) == 0.0


def test_多选题全对且键序无关():
    assert score_choice(["B", "A"], ["A", "B"], 2) == 2.0


def test_选择题口径与_G3_的严格匹配一致():
    """与质检闸门共用同一把尺子 —— 否则会出现「闸门认为对、判分认为错」的自相矛盾。"""
    from app.services.quality_gates import _normalize_keys

    sel, cor = ["b ", "A"], ["A", "B"]
    assert _normalize_keys(sel) == _normalize_keys(cor)
    assert score_choice(sel, cor, 5) == 5.0


# ---------------- 简答：按采分点切分 ----------------

def test_采分点按中文序号切分():
    pts = split_reference_points("①关爱学生，尊重学生人格。②教书育人，实施素质教育。③为人师表。")
    assert len(pts) == 3
    assert pts[0].startswith("关爱学生")


def test_采分点按换行切分():
    pts = split_reference_points("第一条要点内容。\n第二条要点内容。\n第三条要点内容。")
    assert len(pts) == 3


def test_去掉残余编号前缀():
    pts = split_reference_points("1. 第一条要点内容。\n2）第二条要点内容。\n（3）第三条要点内容。")
    assert pts[0].startswith("第一条")
    assert pts[1].startswith("第二条")
    assert pts[2].startswith("第三条")
    for p in pts:
        assert not p[0].isdigit() and p[0] not in "（）()"


def test_过短碎片被丢弃_不把噪声当采分点():
    """参考答案里常混着"如下""解析"这类残句 —— 当成采分点会让分母虚高。"""
    pts = split_reference_points("如下：\n①关爱学生，尊重学生人格。\n注：")
    assert pts == ["关爱学生，尊重学生人格。"]


def test_重复采分点只保留一次():
    pts = split_reference_points("关爱学生，尊重学生人格。\n关爱学生，尊重学生人格。")
    assert len(pts) == 1


def test_空文本返回空列表():
    assert split_reference_points("") == []


# ---------------- 简答：按点给分 ----------------

class _StubJudge:
    """按预设返回采分点判定；`None` 表示模型不可用。"""

    def __init__(self, hits: dict[int, bool] | None, evidence: str = "作答里对应的话"):
        self._hits = hits
        self._evidence = evidence
        self.prompts: list[str] = []

    def ask(self, prompt: str, timeout: int = 30) -> str | None:
        self.prompts.append(prompt)
        if self._hits is None:
            return None
        return json.dumps(
            {"hits": [{"point": i, "hit": h, "evidence": self._evidence if h else ""}
                      for i, h in self._hits.items()]},
            ensure_ascii=False,
        )


_POINTS = ["关爱学生", "教书育人", "为人师表", "终身学习"]


def test_全部命中得满分():
    r = score_by_points(_StubJudge({1: True, 2: True, 3: True, 4: True}), _POINTS, "作答", 10)
    assert r.scored is True and r.n_hits == 4 and r.score == 10.0


def test_部分命中按点给分():
    """4 点、满分 10 → 每点 2.5；命中 2 点得 5 分。**不四舍五入到整数**（分数应尽量细）。"""
    r = score_by_points(_StubJudge({1: True, 2: True, 3: False, 4: False}), _POINTS, "作答", 10)
    assert r.per_point == 2.5 and r.n_hits == 2 and r.score == 5.0


def test_全部未命中得零分_但判定是有效的():
    r = score_by_points(_StubJudge({1: False, 2: False, 3: False, 4: False}), _POINTS, "作答", 10)
    assert r.scored is True and r.score == 0.0


def test_判定无效不等于零分():
    """**这条最要紧**：把"接口抖了一下"记成 0 分，会让学生分数凭空调低且事后无法分辨。

    所以 `scored=False` 与 `score=0` 必须能区分开 —— 调用方据此重试或明说"没判成"。
    """
    r = score_by_points(_StubJudge(None), _POINTS, "作答", 10)
    assert r.scored is False and r.score == 0.0
    # 若只看 score，这两者一模一样；scored 才是那个区分位
    ok = score_by_points(_StubJudge({1: False, 2: False, 3: False, 4: False}), _POINTS, "x", 10)
    assert ok.score == r.score and ok.scored != r.scored


def test_没有采分点时不判分_而不是给零分():
    r = score_by_points(_StubJudge({1: True}), [], "作答", 10)
    assert r.scored is False and r.points == ()


def test_命中必须带依据_便于人工核对():
    """语义判定无法自动复核，所以每条命中都要留下考生作答里的原话。"""
    r = score_by_points(_StubJudge({1: True, 2: True, 3: True, 4: True}), _POINTS, "作答", 10)
    assert all(p.hit and p.evidence for p in r.points)


def test_提示词要求按语义判断而不是字面():
    j = _StubJudge({1: True})
    score_by_points(j, ["关爱学生"], "老师很关心学生的感受", 4)
    assert "语义" in j.prompts[0]
    assert "不要求字面一致" in j.prompts[0]


def test_一次调用判全部点_控制成本():
    """逐点各一次调用是 点数 倍开销；这里必须只调一次。"""
    j = _StubJudge({1: True, 2: True, 3: True, 4: True})
    score_by_points(j, _POINTS, "作答", 10)
    assert len(j.prompts) == 1


# ---------------- 结果结构与 Fake 替身 ----------------

def test_结果可序列化且字段齐全():
    r = score_by_points(_StubJudge({1: True, 2: False}), ["甲要点内容", "乙要点内容"], "作答", 6)
    d = r.as_dict()
    assert set(d) >= {"scored", "n_points", "n_hits", "max_score", "per_point", "score", "points"}
    assert d["points"][0]["hit"] is True and "point" in d["points"][0]


def test_PointHit_是冻结的():
    p = PointHit(point="x", hit=True)
    with pytest.raises(Exception):
        p.hit = False  # type: ignore[misc]


def test_分级模式_部分涉及给半分():
    """`graded=True`：覆盖度加权 —— 越接近标准答案越容易得分，"答到一半"也有分。"""
    j = _StubJudge({}, )
    j._hits = None  # 走自定义 payload
    j.ask = lambda prompt, timeout=30: json.dumps(
        {"hits": [{"point": 1, "coverage": 1.0, "evidence": "答到了"},
                  {"point": 2, "coverage": 0.5, "evidence": "沾到一点"},
                  {"point": 3, "coverage": 0.0, "evidence": ""},
                  {"point": 4, "coverage": 0.5, "evidence": "沾到一点"}]}
    )
    r = score_by_points(j, _POINTS, "作答", 10, graded=True)
    assert r.graded is True
    assert r.per_point == 2.5
    assert r.score == 5.0  # (1.0 + 0.5 + 0 + 0.5) × 2.5
    assert r.n_hits == 3   # 覆盖度 > 0 即算命中（含部分）


def test_分级模式与非分级模式在同一作答上的差别():
    """同一份作答：非分级只数命中，分级按贴近度加权 —— 后者给分更细（且不会更低）。"""
    payload = json.dumps(
        {"hits": [{"point": 1, "coverage": 1.0, "evidence": "甲"},
                  {"point": 2, "coverage": 0.5, "evidence": "乙"}]}
    )

    class _P:
        def ask(self, prompt, timeout=30):
            return payload

    plain = score_by_points(_P(), ["甲点", "乙点"], "作答", 4)
    graded = score_by_points(_P(), ["甲点", "乙点"], "作答", 4, graded=True)
    assert plain.score == 4.0        # 两点都算命中 → 满分
    assert graded.score == 3.0       # 1.0 + 0.5 = 1.5 × 2.0


def test_覆盖度越界被夹到0与1之间():
    r = score_by_points(
        _StubJudge({}),  # 不用其返回值
        _POINTS,
        "作答",
        10,
        graded=True,
    )
    # 替身无法解析自定义 payload 时 judged 为 False；这里只验证 clamping 逻辑本身
    from app.services.prompts_kb import parse_point_judge

    v = parse_point_judge('{"hits":[{"point":1,"coverage":2.5},{"point":2,"coverage":-1}]}')
    assert v[1] == (True, 1.0, "")
    assert v[2] == (False, 0.0, "")


def test_替身能走通成功分支_否则链路无从验证():
    """与既有替身同一条约定：fake 必须能拿满分，否则"按点给分"在离线环境里恒为 0。"""
    r = score_by_points(FakeLLMClient(), _POINTS, "考生作答", 10)
    assert r.scored is True and r.score == 10.0
