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
from eval import sum_g3_slices as sm  # noqa: E402
from eval.g3_slices import LEGACY_SPANS, POLLUTED_TAGS  # noqa: E402


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

    所以它不在 `_LEGACY_USABLE` 里：宁可多跑一遍，不可漏跑。
    """
    monkeypatch.setattr(fill, "_RESULTS", tmp_path)
    _write_md(tmp_path, "ds", "p1b")
    _write_md(tmp_path, "ds", "p3b")
    got, _used = fill._covered_from_legacy("ds")
    assert got == set()
    assert "p1b" not in fill._LEGACY_USABLE
    assert "p3b" not in fill._LEGACY_USABLE


def test_两个脚本读的是同一张账本():
    """账本两份就会分叉：`fill` 知道 p1b 没测完（不计覆盖），`sum` 不知道（当完整算），
    实测报出 `n=287 > 数据集 254 道` —— 覆盖被重复计数。

    所以这条断言守的不是数值，而是**"同一件事实只有一处定义"**：
    污染片必须既在账本里（有声明区间）又被标为不可用。
    """
    assert set(POLLUTED_TAGS) <= set(LEGACY_SPANS), "污染片也得有声明区间，否则无法核对完整性"
    assert set(fill._LEGACY_USABLE) == set(LEGACY_SPANS) - set(POLLUTED_TAGS)


# ---------------- 汇总口径：没测完的片不许进主数字 ----------------


def _slice_md(dir_: pathlib.Path, dataset: str, tag: str, n: int, span: tuple[int, int],
              blocked: int = 0) -> None:
    (dir_ / f"g3_per_option_{dataset}_{tag}.md").write_text(
        f"# 报告\n- 生成时间：2026-06-15　LLM=real　每次投票 3 次\n"
        f"- 数据切片：`x.json` 第 {span[0]}–{span[1]} 道（共 100 道）\n"
        f"| 官方好题（n={n}） | **不该** | 0.0 | **0.0** | 越低越安全 |\n"
        f"- 有效判定 {n} 道，其中被拦 {blocked} 道：\n",
        encoding="utf-8",
    )


def test_没测完的分片不进主数字(tmp_path, monkeypatch, capsys):
    """`p1b` 只测 18/51，它的区间与完整的片重叠 —— 相加就重复计数。

    规则：`n < 声明区间长度` 的片**单列告警、不计入**（与"缺口"那边同一个判断）。
    """
    monkeypatch.setattr(sm, "_RESULTS", tmp_path)
    _slice_md(tmp_path, "ds", "good", 51, (51, 101), blocked=1)
    _slice_md(tmp_path, "ds", "p1b", 18, (0, 50), blocked=2)

    assert sm.main(["ds", "good", "p1b"]) == 0
    out = capsys.readouterr().out
    assert "主数字（real 且测完区间）：n=51 被拦=1" in out
    assert "没测完" in out and "p1b" in out


def test_老分片靠账本才能核对完整性(tmp_path, monkeypatch, capsys):
    """老报告头没写区间，只能查账本 —— 否则只能"假设它完整"，而那正是 n=287 的来源。"""
    monkeypatch.setattr(sm, "_RESULTS", tmp_path)
    (tmp_path / "g3_per_option_ds_p2.md").write_text(
        "# 报告\n- 生成时间：2026-06-15　LLM=real\n"
        "| 官方好题（n=51） | **不该** | 0.0 | **0.0** | 越低越安全 |\n"
        "- 有效判定 51 道，其中被拦 1 道：\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sm, "_dataset_total", lambda _d: 100)

    assert sm.main(["ds", "p2"]) == 0
    out = capsys.readouterr().out
    assert "51–101*" in out               # 区间来自账本
    assert "主数字" in out and "n=51" in out
    assert "覆盖：序号 51–101，去重后 51 道" in out


def test_覆盖不足会明确报缺口而不是含糊过去(tmp_path, monkeypatch, capsys):
    """⚠️ 报告头没写数据集总道数时，必须**去数据集文件数**，不能因为算不出就沉默 ——
    "覆盖了 254 道"这个说法要么被算出来，要么明确报缺口。"""
    monkeypatch.setattr(sm, "_RESULTS", tmp_path)
    (tmp_path / "g3_per_option_ds_a.md").write_text(
        "# 报告\n- 生成时间：2026-06-15　LLM=real\n"
        "- 数据切片：`x.json` 第 30–50 道\n"      # 刻意不带「共 N 道」
        "| 官方好题（n=21） | **不该** | 0.0 | **0.0** | 越低越安全 |\n"
        "- 有效判定 21 道，其中被拦 0 道：\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sm, "_dataset_total", lambda _d: 254)

    sm.main(["ds", "a"])
    out = capsys.readouterr().out
    assert "仍有缺口" in out and "去重后 21 道 / 数据集 254 道" in out


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
