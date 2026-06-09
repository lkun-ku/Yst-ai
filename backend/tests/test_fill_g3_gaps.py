"""`eval/fill_g3_gaps.py` 的记账规则 —— 它决定我们**能不能说"254 道全覆盖"**。

这个脚本的输出没有第二个人核对（它是"还缺哪些题"的唯一来源），所以记账规则必须被钉住：

- **算多**：把没真正测到的区间记成已覆盖 → 缺的题被静默漏掉，报出来的 n 永远不是 254；
- **算少**：把测过的记成没测 → 白花一遍额度。

两个方向的代价不对称，因此**宁可算少**（多跑一遍只是花钱，漏跑是假证据）。
"""

from __future__ import annotations

import json
import pathlib
import sys

_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from eval import fill_g3_gaps as fill  # noqa: E402


def _write_md(dir_: pathlib.Path, stem: str, tag: str, real: bool = True) -> None:
    (dir_ / f"g3_per_option_{stem}_{tag}.md").write_text(
        f"# 报告\n- 生成时间：2026-06-15　LLM={'real' if real else 'fake'}　每次投票 3 次\n",
        encoding="utf-8",
    )


def _write_jsonl(dir_: pathlib.Path, stem: str, tag: str, recs: list[dict]) -> None:
    body = "\n".join(json.dumps(r, ensure_ascii=False) for r in recs)
    (dir_ / f"g3_safety_{stem}_{tag}.jsonl").write_text(body + "\n", encoding="utf-8")


def _dataset(dir_: pathlib.Path, n: int) -> pathlib.Path:
    p = dir_ / "ds.json"
    p.write_text(
        json.dumps({"items": [{"id": f"q{i}"} for i in range(n)]}, ensure_ascii=False),
        encoding="utf-8",
    )
    return p


# ---------------- 区间压缩 ----------------


def test_区间压缩把连续序号并成一段():
    assert fill._ranges({1, 2, 3, 7, 8, 20}) == [(1, 3), (7, 8), (20, 20)]
    assert fill._ranges(set()) == []


# ---------------- 覆盖的三条来源 ----------------


def test_逐题产物按序号记账_老格式按题目id反查(tmp_path, monkeypatch):
    """新产物有 `idx`，老产物（如 s1）只有题目 id —— 两条都要能算出来。"""
    monkeypatch.setattr(fill, "_RESULTS", tmp_path)
    _write_jsonl(tmp_path, "ds", "new", [{"id": "q2", "idx": 2}, {"id": "q0", "idx": 0}])
    _write_jsonl(tmp_path, "ds", "old", [{"id": "q1"}])  # 无 idx → 靠 id 反查

    got, unknown = fill._covered_from_jsonl("ds", {"q0": 0, "q1": 1, "q2": 2})
    assert got == {0, 1, 2}
    assert unknown == 0


def test_归属不明的逐题记录会被单独报出来(tmp_path, monkeypatch):
    """换了数据集时，旧 jsonl 的 id 反查不到 —— 不能悄悄算成"已覆盖"。"""
    monkeypatch.setattr(fill, "_RESULTS", tmp_path)
    _write_jsonl(tmp_path, "ds", "x", [{"id": "别的数据集的题"}])

    got, unknown = fill._covered_from_jsonl("ds", {"q0": 0})
    assert got == set()
    assert unknown == 1


def test_报告头写了区间就直接采信(tmp_path, monkeypatch):
    monkeypatch.setattr(fill, "_RESULTS", tmp_path)
    (tmp_path / "g3_per_option_ds_g30.md").write_text(
        "- 数据切片：`ds.json` 第 30–32 道（共 100 道）\n", encoding="utf-8"
    )
    assert fill._covered_from_md_headers("ds") == {30, 31, 32}


# ---------------- 老分片的记账（本轮踩到的坑）----------------


def test_老分片只在报告存在且为real时计入覆盖(tmp_path, monkeypatch):
    monkeypatch.setattr(fill, "_RESULTS", tmp_path)
    got, used = fill._covered_from_legacy("ds")
    assert got == set() and used == []  # 产物都不在 → 一点覆盖都不认

    _write_md(tmp_path, "ds", "p2")            # real，且在表里
    _write_md(tmp_path, "ds", "p4", real=False)  # fake：数字无意义，不算
    got, used = fill._covered_from_legacy("ds")
    assert {51, 101} <= got and 153 not in got
    assert used == ["p2(51–101)"]


def test_被额度污染的分片不许算成已覆盖(tmp_path, monkeypatch):
    """`p1b` 撞上额度耗尽、只测出 18/51 道。若按"区间已覆盖"记账，
    缺口会被**少算** 21 道 —— 于是报出来的 n 永远到不了 254，而看起来是跑完了。

    所以它刻意不在 `_LEGACY_SLICES` 里：宁可多跑一遍，不可漏跑。
    """
    monkeypatch.setattr(fill, "_RESULTS", tmp_path)
    _write_md(tmp_path, "ds", "p1b")
    _write_md(tmp_path, "ds", "p3b")
    got, _used = fill._covered_from_legacy("ds")
    assert got == set()
    assert "p1b" not in fill._LEGACY_SLICES
    assert "p3b" not in fill._LEGACY_SLICES


# ---------------- 端到端：缺口算法 ----------------


def test_缺口由产物反推而不是靠记忆(tmp_path, monkeypatch, capsys):
    """10 道数据集：0–4 有逐题产物，6–9 由老分片覆盖 → 缺口应只剩第 5 道。"""
    monkeypatch.setattr(fill, "_RESULTS", tmp_path)
    ds = _dataset(tmp_path, 10)
    _write_jsonl(tmp_path, "ds", "s1", [{"id": f"q{i}", "idx": i} for i in range(5)])
    (tmp_path / "g3_per_option_ds_old.md").write_text(
        "- 数据切片：`ds.json` 第 6–9 道（共 10 道）\n", encoding="utf-8"
    )

    rc = fill.main(["--dataset", str(ds)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "已覆盖 9 道" in out
    assert "缺口 1 道" in out
    assert "offset=5 " in out
