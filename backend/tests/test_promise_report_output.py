"""承诺表落盘路径的护栏：**fake 数字不许顶掉真基准**。

## 为什么值得单独测（2026-03-04 真实踩到）

在 fake 下跑一次 `promise_report`（本意只是刷新"评分一致性"那一格），
`唯一性通过率` 那格**真实的 `0.9（18/20）` 被 fake 的 `0.0（0/8）` 覆盖** ——
而表里那行的来源列仍写着「**本次运行**｜硬机制（G3 闸门）」，读起来就是一次真实测量。

`marking_eval` 早就有同款保护（"已有真实基准，本次 fake 结果改写到 marking_fake.md"），
而 `promise_report` 没有 —— 同类问题两个脚本两种行为，正是这个仓库反复吃过的亏。
"""

from __future__ import annotations

import pathlib
import sys

_BACKEND = pathlib.Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from eval import promise_report as pr  # noqa: E402


def test_fake_模式改写到_fake_文件(tmp_path):
    got, warn = pr.output_path(tmp_path / "promise_report.md", "fake")
    assert got.name == "promise_report_fake.md"
    assert "不覆盖真基准" in warn, "必须**说出来**，否则使用者以为写到老地方了"


def test_real_模式写回原文件(tmp_path):
    target = tmp_path / "promise_report.md"
    got, warn = pr.output_path(target, "real")
    assert got == target and warn == ""


def test_显式指定的输出名不被劫持(tmp_path):
    """`--out` 是用户自己指定的路径，规则不该改名 —— 那会让人找不到产物。"""
    target = tmp_path / "my_report.md"
    got, _ = pr.output_path(target, "fake")
    assert got == target


def test_fake_路径与原路径同目录且只改文件名(tmp_path):
    """改名不能顺带换目录：产物必须仍落在 results/ 下，否则没人会去找它。"""
    got, _ = pr.output_path(tmp_path / "promise_report.md", "fake")
    assert got.parent == tmp_path
