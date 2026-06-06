"""三条「承诺写了一个口径、实现测的是另一个」的指标 —— 用**手算期望值**钉住。

对齐审计（2026-06-15）发现 §6.3 有三行**根本测不出承诺那个数**：

1. **Recall@K**：承诺写 K = 1/5/10，实测只到 K = 1/2/3 → 「≥ 0.80 @5」无法对照；
2. **faithfulness**：承诺写「比例 ≥ 0.90」，实现给的是 `factuality` **1–5 分**（连续量）；
3. **唯一性通过率**：被「误杀率」顶替 —— 两者**分母不同**，回答的不是同一个问题。

盯着"数字好不好看"是看不出这些的（三行都各有数字），必须盯**口径**。
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from eval.metrics import (  # noqa: E402
    GatePassMetrics,
    evaluate_gate_pass,
    faithfulness_rate,
    pick_ks,
    recall_at_k,
)


# ---------------- ① K 要随语料规模走 ----------------


def test_小语料只测紧邻档():
    assert pick_ks(6) == (1, 2, 3)
    assert pick_ks(30) == (1, 2, 3, 5)
    assert pick_ks(415) == (1, 2, 3, 5, 10)


def test_k必须小于语料规模_否则该档没有区分度():
    """这是 `pick_ks` 存在的唯一理由，所以直接钉住不变量。"""
    for n in (6, 20, 50, 415):
        assert max(pick_ks(n)) < n, f"n={n} 时选了 k={pick_ks(n)}"


def test_六片语料上recall_at_10恒为一_这正是不取它的理由():
    """黄金片段排在**最后**：`recall@10` 给出 1.0，而 `recall@3` 给出 0.0 ——
    同一份数据、只换 k，结论从"完美"变成"全错"。这就是小语料上取 k=10 的荒谬之处。"""
    hits = [[False, False, False, False, False, True]]  # 6 片语料、黄金在第 6 位
    assert recall_at_k(hits, (3,))[3] == 0.0
    assert recall_at_k(hits, (10,))[10] == 1.0


# ---------------- ② faithfulness 是"比例"而不是"平均分" ----------------


def test_faithfulness用比例而不是平均分():
    """同一个 1–5 分的判分，两种读法给出**不同**的数 —— 承诺写的是比例。

    平均分读法：mean(5,4,3,1) / 5 = 0.65；比例读法（≥4 算达标）：2/4 = 0.5。
    两者都"像"一个 0~1 的数，但只有后者与「≥ 0.90」这条承诺同口径。
    """
    judged = [{"factuality": 5}, {"factuality": 4}, {"factuality": 3}, {"factuality": 1}]
    mean_reading = round(sum(s["factuality"] for s in judged) / len(judged) / 5, 4)
    assert mean_reading == 0.65
    assert faithfulness_rate(judged) == 0.5
    assert mean_reading != faithfulness_rate(judged), "两种读法必须能区分开，否则口径白写"


def test_faithfulness按阈值分档():
    judged = [{"factuality": 4}, {"factuality": 4.0}, {"factuality": 3.9}]
    assert faithfulness_rate(judged, min_factuality=4.0) == round(2 / 3, 4)
    assert faithfulness_rate(judged, min_factuality=3.5) == 1.0


def test_空样本返回零而不是抛异常():
    """空集上的"比例"没有定义 —— 返回 0.0 但**必须由报告说明是空集**，
    否则 0.0 会被读成"一条都没过"。"""
    assert faithfulness_rate([]) == 0.0


def test_缺失或非法分数按不达标处理():
    """判分缺失（模型没给这个维）不能当成满分 —— 那会把失败涂成成功。"""
    assert faithfulness_rate([{}, {"factuality": None}, {"factuality": "abc"}]) == 0.0


# ---------------- ③ 通过率与误杀率：分母不同 ----------------


def test_通过率的分母是全部_与误杀率的分母不同():
    """4 道题里拦掉 1 道（模拟"本该被拦的坏题"）。

    - **通过率** = 3/4 = 0.75（分母：待过闸门的全部）
    - **误杀率**（若把这批题当成"全是好题"来读）= 1/3（分母：好题）——两个数回答的不是一个问题
    """
    payloads = [{"id": i} for i in range(4)]

    def gate(items):
        return [p for p in items if p["id"] != 0], [{"id": 0}]

    kept, m = evaluate_gate_pass(payloads, gate)
    assert [p["id"] for p in kept] == [1, 2, 3]
    assert (m.n_total, m.n_passed, m.pass_rate) == (4, 3, 0.75)


def test_全通过时通过率是一而不是零():
    """一个"什么都不拦"的闸门，通过率是 **1.0** —— 在误杀率口径里也是 1.0 的反面，
    这正是两个指标不能混用的直观例子。"""

    def gate(items):
        return list(items), []

    _, m = evaluate_gate_pass([{"id": 1}, {"id": 2}], gate)
    assert m.pass_rate == 1.0


def test_分母为零时不可测_而不是零分():
    """与 `vote_uniqueness` 同一条原则：**"无法判定"必须与"判定为不合格"分开**。
    空集或全部无法判定时，0.0 会被读成"全军覆没"。"""
    m = GatePassMetrics(n_total=0, n_passed=0)
    assert m.pass_rate == 0.0 and m.measurable is False
    assert GatePassMetrics(n_total=2, n_passed=0).measurable is True


def test_闸门返回空也能算_不抛异常():
    def gate(items):
        return [], [{"_gate": "G3"}]

    kept, m = evaluate_gate_pass([{"id": 1}], gate)
    assert kept == [] and m.pass_rate == 0.0 and m.measurable is True


def test_与真实闸门串起来能算出数(monkeypatch):
    """端到端（fake 环境）：**真闸门** + 新口径 —— 证明这条度量能接到产品代码上，
    而不只是"纯函数自己算得对"。数字本身在 fake 下没有意义。"""
    from app.config import settings
    from app.services.llm_client import FakeLLMClient
    from app.services.quality_gates import apply_uniqueness_gate

    # ⚠️ **显式用 `FakeLLMClient`，不用 `get_llm_client()`**：后者看环境变量吃饭 ——
    # 外层 `LLM_MODE=real` 时用例会去打真实 API（慢且**偷偷花额度**），而断言按 fake 写、
    # 于是「红不了但已经在花钱」。与 conftest 的"测试与仓库数据无关"同一条纪律。
    monkeypatch.setattr(settings, "gate_g3_enabled", True)
    payloads = [
        {"stem": "示例题", "type": "single",
         "options": [{"key": "A", "text": "示例正确表述"}, {"key": "B", "text": "错误表述"}],
         "answer": ["A"]},
        {"stem": "示例简答题", "type": "short", "answer": ["甲要点"]},
    ]
    kept, m = evaluate_gate_pass(
        payloads, lambda items: apply_uniqueness_gate(FakeLLMClient(), items)
    )
    assert m.measurable and m.n_total == 2
    # 简答题**不参与**唯一性判定（对没有唯一答案的题型谈唯一性是概念错误）→ 必被放行
    assert any(p.get("type") == "short" for p in kept)
