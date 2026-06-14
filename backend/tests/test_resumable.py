"""逐题落盘 + 断点续跑（`eval/resumable.py`）的回归测试。

## 为什么值得测

这套机制存在的唯一理由是：**长跑被中断后重跑不白花钱**。而它一旦失效，
症状是"悄悄从零重跑"（不报错、只是又花一遍额度）—— 这类失效**只能靠断言发现**。
（`g3_per_option_eval` 那次实测救过一次：254 道跑到一半中断，重跑只补缺口。）

重点测的是**续跑语义**，不是 json 读写本身：
- 已落盘的题要被**复用**（不重复花调用）；
- 多轮实验里"同一题第 2 轮"**不能**被第 1 轮的记录顶掉（键必须带轮次）；
- 半行/坏行不能让整批续跑失败。
"""

from __future__ import annotations

import json
import pathlib
import sys

_BACKEND = pathlib.Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from eval import resumable  # noqa: E402

_ROW_KEY = lambda r: f"{r.get('id')}#{r.get('repeat', 1)}"  # noqa: E731


def test_已落盘的记录会被复用(tmp_path):
    """续跑的核心：跑过的题不再花调用。"""
    p = tmp_path / "x.jsonl"
    resumable.append_record(p, {"id": "q1", "repeat": 1, "total_std": 3.5})
    done = resumable.load_done(p, key_of=_ROW_KEY)

    assert set(done) == {"q1#1"}
    pending = resumable.todo_keys(
        [{"id": "q1"}, {"id": "q2"}], done, key_of=_ROW_KEY
    )
    assert [it["id"] for it in pending] == ["q2"], "只有没跑过的 q2 该被补跑"


def test_多轮实验里同一题的不同轮次不会被顶掉(tmp_path):
    """**这条守的是一个容易踩的坑**：键若只用题目 id，第 2 轮会被当成"跑过了"直接跳过，
    那一轮就永远跑不到 —— 而报错是没有的，只是数据悄悄少一轮。"""
    p = tmp_path / "x.jsonl"
    resumable.append_record(p, {"id": "q1", "repeat": 1, "total_std": 3.0})
    resumable.append_record(p, {"id": "q1", "repeat": 2, "total_std": 4.0})
    done = resumable.load_done(p, key_of=_ROW_KEY)

    assert set(done) == {"q1#1", "q1#2"}
    assert done["q1#2"]["total_std"] == 4.0


def test_坏行跳过而不拖垮整批(tmp_path):
    """中断可能留下半行。一条坏记录不该让整批续跑失败 —— 那等于没有续跑。"""
    p = tmp_path / "x.jsonl"
    p.write_text(
        '{"id": "q1", "repeat": 1}\n{"id": "q2"  ← 半行坏了\n{"id": "q3", "repeat": 1}\n',
        encoding="utf-8",
    )
    done = resumable.load_done(p, key_of=_ROW_KEY)
    assert set(done) == {"q1#1", "q3#1"}


def test_文件不存在时返回空而不是报错(tmp_path):
    assert resumable.load_done(tmp_path / "nope.jsonl") == {}


def test_追加写入立即可见(tmp_path):
    """进程被 Ctrl+C 杀掉时，已写的行不能丢 —— 所以写完就 flush。"""
    p = tmp_path / "x.jsonl"
    for i in range(3):
        resumable.append_record(p, {"id": f"q{i}", "repeat": 1})
        assert len(resumable.load_done(p, key_of=_ROW_KEY)) == i + 1


def test_tag_决定文件名且不允许混淆(tmp_path):
    """换 tag = 从零跑；同 tag = 续跑。文件名必须带上 tag，否则两次运行会互相污染。"""
    a = resumable.result_path(tmp_path, "marking_consistency_主观题样本", "run1")
    b = resumable.result_path(tmp_path, "marking_consistency_主观题样本", "run2")
    assert a != b and a.name.endswith("run1.jsonl") and b.name.endswith("run2.jsonl")


def test_进度说明在有无历史时都说得清():
    assert "无历史记录" in resumable.summarise({}, [{"id": "q1"}])
    msg = resumable.summarise({"q1#1": {}}, [])
    assert "已有 1 条" in msg and "补 0 条" in msg


def _keys_after(rows) -> set:
    return {_ROW_KEY(r) for r in rows}


def test_落盘记录能被原样读回(tmp_path):
    rec = {"id": "q1", "qtype": "material", "repeat": 2, "per_dim_std": {"relevance": 1.5}}
    p = tmp_path / "x.jsonl"
    resumable.append_record(p, rec)
    got = resumable.load_done(p, key_of=_ROW_KEY)["q1#2"]
    assert got == rec  # 中文与非 ascii 也要原样回来（ensure_ascii=False）
