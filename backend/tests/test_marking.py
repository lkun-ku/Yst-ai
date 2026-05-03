"""主观题批改：rubric 依据、拒批红线、一致性度量。

**为什么单列一组用例**：这是本项目里**唯一会给考生打分**的能力，
而"打分"最容易发生的错是**看起来很正常**：

1. 在没有依据时照常给分 —— 考生以为意见有出处，其实没有；
2. 引用一句依据里找不到的原文 —— "有据"变成口号；
3. 把"依据不足导致的拒批"算进一致性标准差 —— 一个依据问题被伪装成打分不稳。

三条都不是崩溃，都只是安静地给出坏结果。所以逐条钉住。
"""

import json

import pytest

from app.config import settings
from app.services.llm_client import FakeLLMClient
from app.services.marking import (
    DIMENSIONS,
    MarkingResult,
    mark_answer,
    measure_consistency,
    parse_marking,
    rubric_query,
)

_RUBRIC = [
    {
        "id": 1,
        "content": "三、关爱学生。关心爱护全体学生，尊重学生人格，平等公正对待学生。",
        "heading_path": "中小学教师职业道德规范（2008年修订） / 三、关爱学生",
    },
    {
        "id": 2,
        "content": "四、教书育人。遵循教育规律，实施素质教育。循循善诱，诲人不倦，因材施教。",
        "heading_path": "中小学教师职业道德规范（2008年修订） / 四、教书育人",
    },
]


class _StubMarker:
    """按需返回批改 JSON 的假模型。"""

    def __init__(self, payload: dict | str | None) -> None:
        self.payload = payload
        self.calls = 0

    def ask(self, prompt, timeout=30):
        self.calls += 1
        if self.payload is None:
            return None
        if isinstance(self.payload, str):
            return self.payload
        return json.dumps(self.payload, ensure_ascii=False)


def _ok_payload(score: float = 70.0, quote: str = "关心爱护全体学生，尊重学生人格") -> dict:
    return {
        "dimensions": {k: score for k in DIMENSIONS},
        "comments": {k: "评语" for k in DIMENSIONS},
        "deductions": ["论据未结合材料"],
        "suggestions": ["先亮明理论点，再引材料"],
        "citations": [{"quote": quote}],
    }


# ---------------- 依据检索 ----------------

def test_不同题型用不同的检索词():
    """依据由**题型**决定，与考生答得好不好无关 —— 否则是"按答案找支持它的依据"。"""
    assert "材料分析" in rubric_query("material")
    assert "写作" in rubric_query("writing")
    assert rubric_query("没见过的题型") == rubric_query("default")
    assert rubric_query("") == rubric_query("default")


# ---------------- 解析 ----------------

def test_解析_分数一律夹到0到100():
    d = parse_marking(json.dumps({"dimensions": {"relevance": 130, "evidence": -20}}))
    assert d["dimensions"]["relevance"] == 100.0
    assert d["dimensions"]["evidence"] == 0.0


def test_解析_部分维度缺失仍可用():
    d = parse_marking(json.dumps({"dimensions": {"relevance": 60}}))
    assert d["dimensions"] == {"relevance": 60.0}


def test_解析_无维度或非JSON返回None():
    assert parse_marking("嗯，这份作答还可以") is None
    assert parse_marking(json.dumps({"comments": {}})) is None
    assert parse_marking("") is None


# ---------------- 批改与两条红线 ----------------

def test_没有依据就不批():
    """拿无关材料当依据的"有据批改"比不批更糟 —— 它让考生以为意见有出处。"""
    r = mark_answer(FakeLLMClient(), "题", "答", [])
    assert r.refused and not r.grounded
    assert "没有可引用的评分依据" in r.refusal_reason
    assert r.total == 0.0


def test_输出无法解析时拒批而不是编一个():
    r = mark_answer(_StubMarker("这不是 JSON"), "题", "答", _RUBRIC)
    assert r.refused and "无法解析" in r.refusal_reason


def test_引用了依据里找不到的原文就拒批():
    """**红线**：与出题链路的引用校验同一条纪律 —— 带编造依据的批改不能因为"其余都对"而放出去。"""
    payload = _ok_payload(quote="教师应当每学期家访不少于三次")
    r = mark_answer(_StubMarker(payload), "题", "答", _RUBRIC)
    assert r.refused
    assert "找不到的原文" in r.refusal_reason


def test_引用了过短的原文也拒批():
    """过短引用不能作为证据（与 citation 的 min_chars 政策一致）。"""
    r = mark_answer(_StubMarker(_ok_payload(quote="学生")), "题", "答", _RUBRIC)
    assert r.refused


def test_引用的原文标点不同仍可通过():
    """归一化层要宽容：模型把「，」写成「,」不该判为编造。"""
    r = mark_answer(
        _StubMarker(_ok_payload(quote="关心爱护全体学生,尊重学生人格")), "题", "答", _RUBRIC
    )
    assert not r.refused and r.grounded


def test_正常批改_给出四维度与总分():
    r = mark_answer(_StubMarker(_ok_payload(80.0)), "题", "答", _RUBRIC)
    assert r.grounded and not r.refused
    assert set(r.dimensions) == set(DIMENSIONS)
    assert r.total == 80.0
    assert r.deductions and r.suggestions
    assert r.citations


def test_总分是各维度均值():
    payload = _ok_payload()
    payload["dimensions"] = {"relevance": 90, "evidence": 70, "structure": 80, "language": 60}
    r = mark_answer(_StubMarker(payload), "题", "答", _RUBRIC)
    assert r.total == 75.0


def test_替身能走通成功分支():
    """若替身给不出可校验的引用，fake 模式下每次批改都会拒批 —— 链路再也测不到成功分支。"""
    r = mark_answer(FakeLLMClient(), "题", "答", _RUBRIC)
    assert r.grounded and not r.refused
    assert r.citations


# ---------------- 一致性度量 ----------------

def test_全部拒批时返回n为0而不是不一致():
    """否则"依据不足"会被伪装成"打分不稳"—— 两者的修法完全不同。"""
    c = measure_consistency(_StubMarker("不是 JSON"), "题", "答", _RUBRIC, n=3)
    assert c.n == 0 and c.total_std == 0.0 and c.agreement == 0.0


def test_分数完全相同时标准差为零且完全一致():
    c = measure_consistency(_StubMarker(_ok_payload(70.0)), "题", "答", _RUBRIC, n=3)
    assert c.n == 3 and c.total_std == 0.0 and c.agreement == 1.0
    assert set(c.per_dim_std) == set(DIMENSIONS)


class _VaryingMarker:
    """每次给不同分数的模型，用来验证标准差确实被算出来。"""

    def __init__(self, scores: list[float]) -> None:
        self.scores = list(scores)

    def ask(self, prompt, timeout=30):
        s = self.scores.pop(0) if self.scores else 70.0
        return json.dumps(_ok_payload(s), ensure_ascii=False)


def test_分数波动时标准差与一致率被算出来():
    c = measure_consistency(_VaryingMarker([60.0, 80.0, 70.0]), "题", "答", _RUBRIC, n=3)
    assert c.n == 3
    assert c.total_std > 0
    assert c.mean_total == 70.0
    # 60/70/80 都在 70±5 之外的两端 → 只有中间那次算"一致"
    assert c.agreement == 0.3333


def test_替身的一致性恒为零_这不是批改很稳的证据():
    """`LLM_MODE=fake` 下一致性必然为 0 标准差 —— 那只是"替身很稳"。
    真实模型下的稳定性必须用真实模型测（ADR-0019 的已知边界）。"""
    c = measure_consistency(FakeLLMClient(), "题", "答", _RUBRIC, n=3)
    assert c.total_std == 0.0
    assert c.agreement == 1.0


def test_批改结果是不可变快照():
    r = MarkingResult(dimensions={"relevance": 1.0})
    with pytest.raises(Exception):
        r.total = 99.0  # frozen dataclass：一次批改的结论不该被改写


def test_一致性次数由配置决定而非写死(monkeypatch):
    monkeypatch.setattr(settings, "marking_votes", 4)
    stub = _StubMarker(_ok_payload())
    measure_consistency(stub, "题", "答", _RUBRIC)
    assert stub.calls == 4
