"""批改一致性「按题型定档」的**规则**测试（不调模型）。

这套规则的作用是让阈值的来源可查：它决定了"某个题型算不算达标"，
所以规则本身必须被钉住 —— 尤其是**不许上调到承诺之上**那一条。
"""

from __future__ import annotations

import pytest

from eval.marking_eval import (
    PROMISED_MAX_STD,
    RECOMMEND_MARGIN,
    aggregate_by_type,
    recommend_threshold,
)
from app.services.marking import STD_TOLERANCE_BY_TYPE


def test_建议阈值等于实测最大加余量():
    assert recommend_threshold(4.55) == round(4.55 + RECOMMEND_MARGIN, 2)


def test_实测超过承诺时不许上调_而是维持承诺值():
    """**这条是防自欺的护栏**：写作实测 11.44 已经超承诺。

    若把阈值上调到 12，它立刻"达标" —— 但那只是把尺子改短。
    正确做法是标"达不成承诺"。
    """
    assert recommend_threshold(11.44) == PROMISED_MAX_STD
    assert recommend_threshold(20.0) == PROMISED_MAX_STD


def test_实测正好等于承诺时不多给余量():
    assert recommend_threshold(10.0) == PROMISED_MAX_STD


def test_空样本不抛异常():
    assert recommend_threshold(0.0) == RECOMMEND_MARGIN


def _row(qtype: str, std: float, agreement: float = 1.0, n: int = 3) -> dict:
    return {
        "id": f"{qtype}-1",
        "qtype": qtype,
        "repeat": 1,
        "tolerance": 10.0,
        "n": n,
        "agreement": agreement,
        "per_dim_std": {"evidence": std},
    }


def test_按题型汇总_把多轮样本并在一起():
    """分档值当初就栽在"单题单轮"上：1.89 被当成辨析的真实水平，下一轮翻到 4.55。

    所以汇总的单位必须是 **(题 × 维度 × 轮) 样本**，而不是"每题一个数"。
    """
    rows = [_row("analysis", 1.89), _row("analysis", 4.55), _row("analysis", 2.5)]
    agg = aggregate_by_type(rows)
    assert agg["analysis"]["n_samples"] == 3
    assert agg["analysis"]["max_std"] == 4.55
    assert agg["analysis"]["mean_std"] == round((1.89 + 4.55 + 2.5) / 3, 2)


def test_多维度都计入样本():
    r = _row("writing", 3.0)
    r["per_dim_std"] = {"evidence": 11.44, "structure": 8.99}
    agg = aggregate_by_type(r and [r])
    assert agg["writing"]["n_samples"] == 2
    assert agg["writing"]["max_std"] == 11.44
    assert agg["writing"]["meets_promise"] is False, "11.44 超过承诺，必须标达不成"


def test_建议阈值按题型分别给出而不是一个数():
    rows = [_row("analysis", 1.5), _row("short", 5.0), _row("writing", 9.0)]
    agg = aggregate_by_type(rows)
    assert agg["analysis"]["recommended"] == 2.5
    assert agg["short"]["recommended"] == 6.0
    assert agg["writing"]["recommended"] == 10.0


def test_汇总按最大实测降序_最危险的排最前():
    rows = [_row("analysis", 1.5), _row("writing", 9.0)]
    assert list(aggregate_by_type(rows))[0] == "writing"


def test_建议阈值一律不宽于承诺():
    for std in (0.0, 3.0, 9.99, 10.0, 10.01, 50.0):
        assert recommend_threshold(std) <= PROMISED_MAX_STD


def test_当前阈值表里的档位都不宽于承诺():
    assert max(STD_TOLERANCE_BY_TYPE.values()) <= PROMISED_MAX_STD


@pytest.mark.parametrize("qtype", ["analysis", "writing", "short", "material", "design"])
def test_每个真实题型都有当前阈值(qtype):
    """数据集里出现过的题型都必须有档位 —— 否则会静默落到 default。"""
    assert qtype in STD_TOLERANCE_BY_TYPE
