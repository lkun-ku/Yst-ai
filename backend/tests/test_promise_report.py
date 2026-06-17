"""`eval/promise_report.py` 的解析与判定逻辑。

**为什么这些也要测**：这个脚本的作用是"让人不必手工对齐承诺与证据" ——
若它自己的判定写错（比如把"没有 @5"读成"@5 达标"），它会**把错的东西写成一张看起来很正式的表**，
比没有这张表更糟。所以判定规则逐条用手算期望值钉住。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from eval import promise_report as pr  # noqa: E402


def _write(tmp_path: Path, name: str, payload: dict) -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return p


# ---------------- 检索：K 口径与 MRR ----------------


def _retrieval_payload(ks: list[int], prod: dict, extra: dict | None = None) -> dict:
    rows = {"⑤ RRF(命中数)+标题 ★生产": prod}
    rows.update(extra or {})
    return {
        "metadata": {"time": "2026-03-04T02:41:29", "ks": ks, "n_chunks": 415, "n_queries": 135},
        "rows": rows,
    }


def test_没有k5时判未知而不是判达标(tmp_path):
    """承诺写 K=1/5/10，证据只有 1/2/3 —— 必须说"无法对照"，不能说"达标"。"""
    p = _write(tmp_path, "r.json", _retrieval_payload([1, 2, 3], {"recall@1": 0.31, "mrr": 0.45}))
    rows = pr.from_retrieval(p)
    recall = rows[0]
    assert recall.verdict == pr._WARN
    assert "@5" in recall.note and "pick_ks" in recall.note


def test_有k5时按0_80判定(tmp_path):
    good = _write(tmp_path, "g.json",
                  _retrieval_payload([1, 5], {"recall@1": 0.8, "recall@5": 0.9, "mrr": 0.7}))
    bad = _write(tmp_path, "b.json",
                 _retrieval_payload([1, 5], {"recall@1": 0.3, "recall@5": 0.5, "mrr": 0.7}))
    assert pr.from_retrieval(good)[0].verdict == pr._OK
    assert pr.from_retrieval(bad)[0].verdict == pr._BAD


def test_MRR按0_60判定并指出精排的作用(tmp_path):
    p = _write(tmp_path, "r.json", _retrieval_payload(
        [1, 2, 3], {"recall@1": 0.31, "mrr": 0.4565},
        {"⑨ sparse→精排(含标题)": {"mrr": 0.8753}},
    ))
    mrr = pr.from_retrieval(p)[1]
    assert mrr.verdict == pr._BAD
    assert "0.8753" in mrr.note and "精排默认关闭" in mrr.note


def test_证据文件不存在时判待跑而不是编数字(tmp_path):
    rows = pr.from_retrieval(tmp_path / "不存在.json")
    assert rows[0].verdict == pr._NONE and "待跑" in rows[0].measured


def test_证据文件损坏时如实说解析失败(tmp_path):
    p = tmp_path / "broken.json"
    p.write_text("{ 这不是 json", encoding="utf-8")
    rows = pr.from_retrieval(p)
    assert rows[0].verdict == pr._NONE
    assert "解析失败" in rows[0].source or "待跑" in rows[0].measured


def test_没有生产档时退回最后一档而不是丢空(tmp_path):
    payload = _retrieval_payload([1, 2, 3], {"recall@1": 0.1, "mrr": 0.1})
    payload["rows"] = {"⑦ 别的档": {"recall@1": 0.5, "mrr": 0.5}}
    rows = pr.from_retrieval(_write(tmp_path, "r.json", payload))
    assert "0.5" in rows[0].measured or "0.5" in rows[1].measured


# ---------------- 问答：拒答率与成本 ----------------


def _teacher_payload(answered: int, n_un: int = 5, cite: float = 1.0, avg=1.8) -> dict:
    return {
        "metadata": {"time": "2026-03-04T19:20:59"},
        "rows": {
            "grounded": {"n_answerable": 20, "n_unanswerable": n_un, "answered": answered,
                         "cite_first_pass_rate": cite, "avg_tool_calls": avg},
            "agent": {"avg_tool_calls": 1.2},
        },
    }


def test_拒答率按区间判定而不是越高越好(tmp_path):
    ok = pr.from_teacher(_write(tmp_path, "a.json", _teacher_payload(answered=20)))     # 5/25 = 0.2
    low = pr.from_teacher(_write(tmp_path, "b.json", _teacher_payload(answered=25)))    # 0/25 = 0.0
    high = pr.from_teacher(_write(tmp_path, "c.json", _teacher_payload(answered=10)))   # 15/25 = 0.6
    assert ok[1].verdict == pr._OK
    assert low[1].verdict == pr._BAD and high[1].verdict == pr._BAD, "过低（硬编）与过高都要判不合格"


def test_成本因缺P95只给警告(tmp_path):
    cost = pr.from_teacher(_write(tmp_path, "a.json", _teacher_payload(answered=20)))[2]
    assert cost.verdict == pr._WARN and "P95" in cost.note


def test_引用通过率按0_90判定(tmp_path):
    bad = pr.from_teacher(_write(tmp_path, "a.json", _teacher_payload(answered=20, cite=0.85)))
    assert bad[0].verdict == pr._BAD


# ---------------- 批改：两个口径都过才算达标 ----------------


def _marking_payload(agree: float, std: float, by_type: dict | None = None) -> dict:
    return {"metrics": {"min_agreement": agree, "max_dim_std": std, "n_rows": 21,
                        "by_type": by_type or {}}}


def test_评分一致性两个口径都过才算达标(tmp_path):
    both_ok = pr.from_marking(_write(tmp_path, "a.json", _marking_payload(0.85, 9.0)))
    only_agree = pr.from_marking(_write(tmp_path, "b.json", _marking_payload(0.9, 13.59)))
    only_std = pr.from_marking(_write(tmp_path, "c.json", _marking_payload(0.3333, 9.0)))
    assert both_ok.verdict == pr._OK
    assert only_agree.verdict == pr._BAD and only_std.verdict == pr._BAD


def test_评分一致性指出未达标题型(tmp_path):
    row = pr.from_marking(_write(tmp_path, "a.json", _marking_payload(
        0.3333, 13.59,
        {"material": {"meets_promise": False, "max_std": 13.59},
         "writing": {"meets_promise": False, "max_std": 13.37}},
    )))
    assert "material" in row.note and "writing" in row.note


# ---------------- 渲染与现场跑 ----------------


def test_渲染出承诺表需要的列并可被机器读(tmp_path):
    md = pr.render([pr.Row("示例", "≥ 1", "0.5", "`x.json`｜硬机制", pr._BAD, "备注")], mode="fake")
    assert "| 指标 | 目标 | 实测 | 来源 | 判定 | 备注 |" in md
    assert "LLM 模式：`fake`" in md
    assert "硬机制" in md and "LLM judge" in md, "来源口径必须写进表头说明"


def test_现场跑闸门在fake下不给判定_只证明口径(tmp_path, monkeypatch):
    """fake 的数字没有参考价值 —— 判定必须是 `—`，否则会被当成真实产出率。

    ⚠️ **刻意直接构造 `FakeLLMClient`，不用 `get_llm_client()`**：
    后者看环境变量吃饭 —— 在外层 shell 里 `LLM_MODE=real` 时，这个用例会去打**真实 API**
    （实测每个 15~20 秒、并偷偷消耗额度），而断言又是按 fake 写的，**红不了但已经在花你的钱**。
    这与 `conftest` 里"测试必须与仓库数据无关"是**同一条纪律**：测试不许依赖环境状态。
    """
    from app.config import settings
    from app.services.llm_client import FakeLLMClient

    monkeypatch.setattr(settings, "gate_g3_enabled", False)  # 默认关，函数内部会临时打开
    row, metrics = pr.from_gate_live(
        [{"stem": "示例题", "type": "single",
          "options": [{"key": "A", "text": "示例正确表述"}, {"key": "B", "text": "错"}],
          "answer": ["A"]}],
        FakeLLMClient(), "fake",
    )
    assert row.verdict == pr._NONE
    assert "无参考价值" in row.note
    assert metrics["n"] == 1, "返回的指标要能直接落盘（且与那一行同源）"
    assert settings.gate_g3_enabled is False, "临时打开后必须还原，不能改全局状态"


def test_gate样本来自仓库内的真题集而不是dev_db():
    sample = pr._gate_sample(3)
    assert sample and all("stem" in s for s in sample)


@pytest.mark.parametrize("n", [1, 8])
def test_gate样本数可控(n):
    assert len(pr._gate_sample(n)) == n


# ---------------- 闸门结果落成证据：数字不能只活在一张会重刷的表里 ----------------


def test_闸门证据可回读且判定与现场跑一致(tmp_path):
    """**这条钉的是"一个数字别只活在一张会重刷的表里"**（2026-03-04）。

    现场跑出的 `0.9（18/20）` 原先只写在报告里，而报告是会被重刷的 ——
    real 模式下不带 `--live-gate` 刷一次，那格就变成「（未现场跑）」，一条真实测量永久消失。
    现在它落成 `g3_gate_pass.json`，刷新报告可以只读它。
    """
    metrics = {"n": 20, "passed": 18, "pass_rate": 0.9, "measurable": True}
    live = pr.gate_row(metrics, "real")
    out = pr.write_gate_evidence(tmp_path / "g3_gate_pass.json", metrics, "real", 20)
    back = pr.gate_evidence_row(out)

    assert out.name == "g3_gate_pass.json"
    assert back is not None
    assert (back.measured, back.verdict, back.target) == (live.measured, live.verdict, live.target)
    assert "g3_gate_pass.json" in back.source, "从证据回读时来源必须写文件名（人要知道这是哪次）"
    assert "本次运行" not in back.source


def test_没有闸门证据时回读返回None而不是编一行(tmp_path):
    """"没有证据"与"测了 0 分"是两件事 —— 由调用方决定怎么说（与 `summarise` 同一纪律）。"""
    assert pr.gate_evidence_row(tmp_path / "g3_gate_pass.json") is None


def test_闸门证据的fake版本不覆盖真证据(tmp_path):
    real = tmp_path / "g3_gate_pass.json"
    pr.write_gate_evidence(real, {"n": 1, "passed": 0, "pass_rate": 0.0, "measurable": True},
                           "fake", 1)

    assert not real.exists(), "fake 不许写真证据文件（与报告文件同一条规矩）"
    assert (tmp_path / "g3_gate_pass_fake.json").exists()


class _StubClient:
    """只为让 `gather` 判成 **real** 模式：real 且不带 `--live-gate` 时它不该发任何调用。"""

    def ask(self, prompt: str) -> str:  # noqa: ANN001
        raise AssertionError("real 分支（未现场跑）不该发起任何调用")


def _gather_rows(tmp_path, monkeypatch) -> dict[str, pr.Row]:
    """把 `gather` 的闸门证据路径指到 tmp，并按名字取回各行（其余证据行只读仓库文件）。"""
    monkeypatch.setattr(pr, "GATE_EVIDENCE", tmp_path / "g3_gate_pass.json")
    monkeypatch.setattr("app.services.llm_client.get_llm_client", lambda: _StubClient())
    rows, mode = pr.gather(live_gate=False, gate_n=3)
    assert mode == "real"
    return {r.name: r for r in rows}


def test_real刷新报告_没有证据时标未跑并指出缺哪份文件(tmp_path, monkeypatch):
    gate = _gather_rows(tmp_path, monkeypatch)["唯一性通过率"]

    assert gate.verdict == pr._NONE and "未现场跑" in gate.measured
    assert "g3_gate_pass.json" in gate.note, "要告诉人缺的是哪份文件，否则他只会看到「未跑」"


def test_real刷新报告_有证据时复用它而不是把它顶掉(tmp_path, monkeypatch):
    monkeypatch.setattr(pr, "GATE_EVIDENCE", tmp_path / "g3_gate_pass.json")
    pr.write_gate_evidence(tmp_path / "g3_gate_pass.json",
                           {"n": 20, "passed": 18, "pass_rate": 0.9, "measurable": True},
                           "real", 20)
    monkeypatch.setattr("app.services.llm_client.get_llm_client", lambda: _StubClient())

    rows, _ = pr.gather(live_gate=False, gate_n=3)
    gate = {r.name: r for r in rows}["唯一性通过率"]

    assert gate.verdict == pr._OK and "0.9" in gate.measured
    assert "g3_gate_pass.json" in gate.source and "本次运行" not in gate.source, (
        "复用证据时来源必须指向那份文件 —— 否则一个 3 小时前的测量看起来像刚跑的"
    )
