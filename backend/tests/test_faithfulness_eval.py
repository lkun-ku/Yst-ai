"""`eval/faithfulness_eval.py` 的**产物自洽性**：`items` 必须真的是「跑过的那几条」。

## 为什么值得一条测试（2026-06-16 实测产物为证）

已入库的 `results/faithfulness.json` 出现过一个**自相矛盾**的状态：
`metadata.n_judged = 12`、`metrics.faithfulness_rate = 1.0`，而 `items` 只有 **1** 条。

根因不是数字错，而是**聚合漏记**：断点续跑时 `done` 是开跑前从 jsonl 读到的快照，
循环里新算的记录只推进了 `judged`、**没有并回 `done`**，而 `items` 取的是 `done.values()`。
于是 `items` 永远只反映「续跑前已存在的那几条」：

- 本次新算 11 条 → `items` 剩 1 条（就是上面那次）；
- 「无历史记录」的全新跑 → `items` 会是**空的**，而表头仍写着 12 条判分。

比例本身没算错（它读 `judged`），但 `items` 是**给人复核比例怎么来的**用的原始记录——
丢了它就等于「数字在、依据不在」，正是本项目反复吃过的亏。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from eval import faithfulness_eval as fe  # noqa: E402
from eval import resumable  # noqa: E402


class _StubClient:
    """替身：判分解析被换掉，所以 `ask` 的返回值我们不关心，也**不联网**。"""

    def ask(self, prompt: str) -> str:  # noqa: ANN001
        return "{}"


@pytest.fixture
def _hermetic(tmp_path, monkeypatch):
    """把 `main()` 的所有外部依赖替掉：不建库、不读语料、不联网。

    这样测的就是**聚合逻辑本身**，不掺模型行为 —— 与 `test_promise_report` 同一条纪律。
    """
    monkeypatch.setattr(fe, "_DB", tmp_path / "eval.db")
    monkeypatch.setattr(fe, "_RESULTS", tmp_path / "results")
    monkeypatch.setattr(fe, "init_db", lambda: None)
    monkeypatch.setattr(fe, "ingest_official_corpus", lambda db, embed=False: None)
    monkeypatch.setattr(fe, "SessionLocal", lambda: None)
    monkeypatch.setattr(fe, "get_llm_client", lambda: _StubClient())
    monkeypatch.setattr(fe, "parse_judge", lambda raw: {"factuality": 5.0})
    monkeypatch.setattr(
        fe, "_answer_and_basis",
        lambda db, q, client: {"question": q, "answer": f"{q}｜答案", "basis": "依据"},
    )
    return tmp_path


def _run(tmp_path: Path, monkeypatch, questions: list[str], tag: str,
         seed: list[dict] | None = None) -> dict:
    """跑一次 `main()`；`seed` 用来模拟「上次中断已落盘几条」。"""
    monkeypatch.setattr(fe, "_questions", lambda db, limit: list(questions))
    if seed:
        jsonl = resumable.result_path(tmp_path / "results", "faithfulness", tag)
        for rec in seed:
            resumable.append_record(jsonl, rec)
    return fe.main(limit=len(questions), tag=tag)


def _written(tmp_path: Path, name: str = "faithfulness.json") -> dict:
    return json.loads((tmp_path / "results" / name).read_text(encoding="utf-8"))


# ---------------- 回归本体：items 不许丢掉本次新算的记录 ----------------


def test_续跑时items要包含本次新算的记录(_hermetic, monkeypatch):
    """**这条钉的就是那次实测**：jsonl 已有 1 条，本次再补 2 条 → items 必须是 3。

    修之前这里是 1（新补的 2 条只进了 `judged`，没进 `items`），而 `n_judged` 是 3 ——
    产物自己和自己对不上。
    """
    qs = ["Q1", "Q2", "Q3"]
    result = _run(_hermetic, monkeypatch, qs, "t1", seed=[{"question": "Q1", "answer": "旧的",
                                                          "judged": {"factuality": 4.0}}])

    assert result["metadata"]["n_judged"] == 3
    assert len(result["items"]) == 3, "items 丢了本次新算的记录 —— 产物会自相矛盾"
    assert {r["question"] for r in result["items"]} == set(qs)


def test_全新跑时items不能是空的(_hermetic, monkeypatch):
    """没有历史记录时，修之前 `items` 是 `[]`（`done` 全程为空）—— 表头写 2 条、脚下列 0 条。"""
    result = _run(_hermetic, monkeypatch, ["Q1", "Q2"], "t2")

    assert result["metadata"]["n_judged"] == 2
    assert len(result["items"]) == 2


def test_落盘文件与返回值一致(_hermetic, monkeypatch):
    """测写出来的**文件**，而不是只看返回值 —— 被引用的是文件（promise_report 读它）。"""
    qs = ["Q1", "Q2", "Q3"]
    _run(_hermetic, monkeypatch, qs, "t3", seed=[{"question": "Q1", "answer": "旧的",
                                                 "judged": {"factuality": 4.0}}])
    data = _written(_hermetic)

    assert len(data["items"]) == data["metadata"]["n_judged"] == len(qs)
    assert data["metrics"]["n"] == len(qs)


def test_拒答记录也要进items(_hermetic, monkeypatch):
    """拒答不计入**比例**，但它是确实跑过的一条 —— 复核时要看得见「哪几题拒答了」。"""
    monkeypatch.setattr(fe, "_answer_and_basis", lambda db, q, client: None)
    result = _run(_hermetic, monkeypatch, ["Q1", "Q2"], "t4")

    assert result["metadata"]["n_judged"] == 0 and result["metadata"]["n_refused"] == 2
    assert len(result["items"]) == 2 and all(r.get("refused") for r in result["items"])


def test_items与逐题jsonl同源(_hermetic, monkeypatch):
    """产物里的每条都应能在 jsonl 里找到 —— 两处不一致时，人不知道该信哪个。"""
    qs = ["Q1", "Q2", "Q3"]
    _run(_hermetic, monkeypatch, qs, "t5", seed=[{"question": "Q1", "answer": "旧的",
                                                 "judged": {"factuality": 4.0}}])
    jsonl = resumable.result_path(_hermetic / "results", "faithfulness", "t5")
    on_disk = [json.loads(l) for l in jsonl.read_text(encoding="utf-8").splitlines() if l.strip()]

    assert len(on_disk) == 3, "jsonl 与 items 的条数必须一致（同一次运行的两种视图）"


def test_进度行报的是待补条数而不是总题数(_hermetic, monkeypatch, capsys):
    """踩到过：`summarise(done, questions)` 把**总题数**当成了待补数。

    实测那行是「已有 12 条结果，本次补 12 条」—— 已全部跑完却说还要补 12 条，
    与 `summarise` 的契约（第二参是待跑条目）不符，也把「其实一条没补」这件事盖住了。
    """
    qs = ["Q1", "Q2", "Q3"]
    _run(_hermetic, monkeypatch, qs, "t7", seed=[{"question": "Q1", "answer": "旧的",
                                                 "judged": {"factuality": 4.0}}])
    assert "已有 1 条结果，本次补 2 条" in capsys.readouterr().out

    _run(_hermetic, monkeypatch, qs, "t7")          # 同 tag 重跑：已全跑完 → 补 0 条
    assert "已有 3 条结果，本次补 0 条" in capsys.readouterr().out


# ---------------- 护栏：fake 不许覆盖真证据 ----------------


def test_fake模式改写文件名不覆盖真证据(_hermetic, monkeypatch):
    from app.services.llm_client import FakeLLMClient

    monkeypatch.setattr(fe, "get_llm_client", lambda: FakeLLMClient())
    _run(_hermetic, monkeypatch, ["Q1"], "t6")

    assert not (_hermetic / "results" / "faithfulness.json").exists(), "fake 不许写真证据文件"
    assert _written(_hermetic, "faithfulness_fake.json")["metadata"]["llm"].startswith("LLM=fake")
