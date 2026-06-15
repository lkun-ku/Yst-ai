"""`eval/marking_eval.py` 的**续跑进度行**：第二参必须是真待补条目，不能是占位。

## 为什么值得一条测试（2026-06-16 实测为证）

那行原本写的是 `resumable.summarise(done, [])` —— 第二参被硬编码成空列表，于是：

    ↻ 断点续跑：已有 1 条结果，本次补 0 条

而同一份代码的循环里明明还要补 3 条。**它是操作者判断"还要跑多久、还要花多少额度"的
唯一依据**，而这个评测是 **3 倍开销**（同一作答独立批改 N 遍）、按额度计费的续跑任务 ——
报"补 0 条"会让人以为快跑完了。

`summarise` 自己的契约没问题（`tests/test_resumable.py` 钉着"第二参是待跑条目"，
`summarise({}, [])` 也确实该说"补 0 条"）—— **坏的是调用点**：它传了一个常量。
这与 `faithfulness_eval.py` 那次（把总题数当待补数）是同一类错：**契约写对了，
但调用点给参数的方式不对**，所以守卫必须落在调用点上。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.services.marking import ConsistencyReport, std_tolerance  # noqa: E402
from eval import marking_eval as me  # noqa: E402
from eval import resumable  # noqa: E402

#: 样本：2 道题。配合 `repeat=2` 就是 2 × 2 = 4 条待跑记录。
_ITEMS = [
    {"id": 1, "qtype": "material", "stem": "题干一", "answer": "作答一"},
    {"id": 2, "qtype": "material", "stem": "题干二", "answer": "作答二"},
]


def _rep() -> ConsistencyReport:
    return ConsistencyReport(
        n=3, per_dim_std={"relevance": 1.0}, total_std=1.0, mean_total=80.0, agreement=1.0
    )


def _rec(id_: int, repeat: int) -> dict:
    """一条**形状完整**的记录 —— 续跑时它会被直接复用并参与聚合，缺字段会在聚合里炸。"""
    return {
        "id": id_, "qtype": "material", "repeat": repeat,
        "tolerance": std_tolerance("material"), **_rep().as_dict(),
    }


@pytest.fixture
def _hermetic(tmp_path, monkeypatch):
    """替掉 `main()` 的全部外部依赖：不建库、不读语料、不调模型。"""
    ds = tmp_path / "ds.json"
    ds.write_text(json.dumps({"items": _ITEMS}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(me, "_DATASET", ds)
    monkeypatch.setattr(me, "_DB", tmp_path / "m.db")
    monkeypatch.setattr(me, "_RESULTS", tmp_path / "results")
    monkeypatch.setattr(me, "_OUT_DIR", tmp_path / "results")
    monkeypatch.setattr(me, "init_db", lambda: None)
    monkeypatch.setattr(me, "SessionLocal", lambda: None)
    monkeypatch.setattr(me, "ingest_official_corpus", lambda db, embed=False: None)
    monkeypatch.setattr(me, "retrieve_rubric", lambda *a, **k: [])
    monkeypatch.setattr(me, "measure_consistency", lambda *a, **k: _rep())
    from app.services.llm_client import FakeLLMClient

    monkeypatch.setattr(me, "get_llm_client", lambda: FakeLLMClient())
    return tmp_path


def _jsonl(tmp_path: Path, tag: str = "run") -> Path:
    return resumable.result_path(tmp_path / "results",
                                 f"marking_consistency_{me._DATASET.stem}", tag)


# ---------------- 回归本体 ----------------

def test_部分完成时进度行报的是真待补数(_hermetic, monkeypatch, capsys):
    """**这条钉的就是那个缺陷**：jsonl 已有 1 条（第 1 轮第 1 题），本次应补 3 条。

    第二参是 `[]` 时这里恒为「本次补 0 条」—— 红。
    """
    resumable.append_record(_jsonl(_hermetic), _rec(1, 1))

    me.main(limit=0, repeat=2, tag="run")
    out = capsys.readouterr().out

    assert "已有 1 条结果，本次补 3 条" in out, f"进度行没报出真实缺口：\n{out}"


def test_全部跑完后进度行才说补0条(_hermetic, monkeypatch, capsys):
    """4 条全在时**才**该说「补 0 条」—— 顺带证明上一测不是靠"永远是 0"通过的。"""
    me.main(limit=0, repeat=2, tag="run")
    capsys.readouterr()                                  # 丢掉第一次的输出
    me.main(limit=0, repeat=2, tag="run")

    assert "已有 4 条结果，本次补 0 条" in capsys.readouterr().out


def test_无历史时不打印续跑行(_hermetic, monkeypatch, capsys):
    """没有历史记录就没什么可"续"的 —— 保持原行为（只有 `if done:` 才打印）。"""
    me.main(limit=0, repeat=2, tag="fresh")

    assert "断点续跑" not in capsys.readouterr().out


def test_补的条数等于实际新增的落盘条数(_hermetic, monkeypatch, capsys):
    """进度行的"本次补 N 条"必须与 jsonl **实际新增**的条数一致 —— 否则它还是句没依据的话。"""
    resumable.append_record(_jsonl(_hermetic), _rec(2, 2))
    before = len(_jsonl(_hermetic).read_text(encoding="utf-8").splitlines())

    me.main(limit=0, repeat=2, tag="run")
    out = capsys.readouterr().out
    after = len(_jsonl(_hermetic).read_text(encoding="utf-8").splitlines())

    assert "已有 1 条结果，本次补 3 条" in out
    assert after - before == 3, "说补 3 条就必须真的新增 3 条"
