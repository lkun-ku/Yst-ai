"""批改一致性的**按题型分档阈值**测试（不调模型）。

守两条：① 辨析（`analysis`）不再落到 `default` 的通用检索词 —— 它此前静默走 default，
于是辨析题检索到的是泛泛的"评分要点"而非"判断正误 + 说明理由"类依据；
② 分档阈值**一律不宽于**现行承诺换算值 10.0 —— 分档是让承诺变具体，
不是给最差的一档开后门（写作实测 9.09 仍按 10.0 判，余量最薄这件事要被看见）。
"""

from __future__ import annotations

import pytest

from app.services.marking import (
    AGREEMENT_MIN,
    RUBRIC_QUERY,
    STD_TOLERANCE_BY_TYPE,
    rubric_query,
    std_tolerance,
)

#: 计划 §6 承诺换算到百分制的等价阈值（0.5 / 5 分制 × 100 = 10）
_PROMISE = 10.0


def test_辨析有专属检索词而不是落到default():
    """辨析是科目一常见题型，用通用检索词等于检索不到它要的依据。"""
    assert "analysis" in RUBRIC_QUERY
    q = rubric_query("analysis")
    assert q != RUBRIC_QUERY["default"], "辨析落到了 default —— 检索到的是泛泛的评分要点"
    assert "辨析" in q and "理由" in q


def test_未知题型回落default而不是抛异常():
    assert rubric_query("不存在的题型") == RUBRIC_QUERY["default"]


@pytest.mark.parametrize("qtype", ["analysis", "design", "short", "material", "writing", "default"])
def test_分档阈值一律不宽于现行承诺(qtype):
    """这条是防"调阈值把不达标改成达标"的护栏。

    阈值可以**更严**（辨析 3.0 就是更严），但不能比承诺换算值更松 ——
    否则分档就变成了给最差的一档开后门。
    """
    assert std_tolerance(qtype) <= _PROMISE, f"{qtype} 的阈值宽于承诺：{std_tolerance(qtype)}"


def test_写作的阈值保持承诺值_余量最薄这件事要被看见():
    """写作实测最大维度标准差 9.09。若把阈值放宽到 11，它就"稳"了 ——
    那条路是错的：应当标出余量最薄并盯着它，而不是调阈值。
    """
    assert std_tolerance("writing") == _PROMISE
    assert round(_PROMISE - 9.09, 2) == 0.91  # 实测余量，用于报告里标红


def test_分档反映题型梯度_越主观越松但仍不越承诺():
    """梯度方向必须与实测一致（辨析最稳、写作最飘），否则阈值是凭感觉定的。"""
    order = ["analysis", "design", "short", "material", "writing"]
    vals = [std_tolerance(t) for t in order]
    assert vals == sorted(vals), f"阈值梯度与实测不一致：{list(zip(order, vals))}"
    assert max(STD_TOLERANCE_BY_TYPE.values()) == _PROMISE


def test_未知题型取default阈值():
    assert std_tolerance("") == STD_TOLERANCE_BY_TYPE["default"]
    assert std_tolerance(None) == STD_TOLERANCE_BY_TYPE["default"]


def test_一致率承诺存在且是0点8():
    assert AGREEMENT_MIN == 0.80
