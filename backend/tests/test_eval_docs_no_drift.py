"""防"索引与报告两处维护"的漂移守卫。

## 为什么要有这条测试

`docs/eval/README.md`（索引）与 `backend/eval/results/promise_report.md`（自动生成）
曾经**各写了一份实测数字** —— 于是变成两处维护，迟早不一致。而"两处说法不同"
正是本仓反复踩的那类问题（§5 第 15 项记了三次同类）。

所以规则是：**索引只写"该看哪个文件"，数字只在报告里**。这条规则若只写在文档里，
下一个人抄数字进来时没人会拦 —— 必须由测试挡住。

## 判定方式

`README.md` 里**除了目标值本身，不允许出现其它小数**：
- 目标值是固定的三个（`0.80` / `0.60` / `0.90`）—— 它们本来就是承诺的一部分；
- 任何**其它**小数都只可能来自"把某次实测读数抄了进来"，也就必然会在下次重跑后过期。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
README = _ROOT.parent / "docs" / "eval" / "README.md"
REPORT = _ROOT / "eval" / "results" / "promise_report.md"

#: 允许出现在索引里的小数 —— 只有**目标值本身**（承诺的一部分，不随运行变化）。
_ALLOWED_DECIMALS = {"0.80", "0.60", "0.90"}


def _strip_refs(text: str) -> str:
    """剥掉**引用形态**的编号（`§6.3`、`§1.3.4`）再扫描。

    它们长得像小数但不是读数。**不能靠加白名单解决** —— 白名单会把守卫一起削弱；
    剥掉引用则让"其余任何小数都是抄进来的读数"这条规则仍然成立。
    """
    return re.sub(r"§\s*\d+(?:\.\d+)*", "", text)


def _decimals(text: str) -> set[str]:
    return set(re.findall(r"(?<![\d.])\d+\.\d+(?![\d.])", _strip_refs(text)))


def test_索引存在且指向报告():
    assert README.exists(), "索引文件不见了"
    text = README.read_text(encoding="utf-8")
    assert "promise_report.md" in text, "索引必须指出数字在哪份报告里"
    assert "eval/promise_report.py" in text, "索引必须给出产出数字的命令"


def test_索引里不许出现实测小数():
    """只允许目标值；其它小数一律视为"抄进来的读数"。"""
    text = README.read_text(encoding="utf-8")
    unexpected = _decimals(text) - _ALLOWED_DECIMALS
    assert not unexpected, (
        f"索引里出现了实测小数 {sorted(unexpected)} —— 数字只应写在 promise_report.md 里，"
        "否则下次重跑后两处必然不一致"
    )


@pytest.mark.skipif(not REPORT.exists(), reason="报告尚未生成（先跑 eval/promise_report.py）")
def test_索引没有把报告里的读数抄回去():
    """更直接的一条：报告**实测列**里出现的小数，索引里一个都不该有。"""
    report = REPORT.read_text(encoding="utf-8")
    measured: set[str] = set()
    for line in report.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 6 and cells[0] not in ("指标", "---"):
            measured |= _decimals(cells[2])  # 第 3 列 = 实测
    readme = README.read_text(encoding="utf-8")
    overlap = measured & _decimals(readme)
    assert not overlap, f"索引抄了报告里的读数：{sorted(overlap)}"


def test_报告本身带来源与判定列():
    """报告必须能自证"数字从哪来、达标没有" —— 否则它只是另一张会过期的表。"""
    if not REPORT.exists():
        pytest.skip("报告尚未生成")
    report = REPORT.read_text(encoding="utf-8")
    assert "| 指标 | 目标 | 实测 | 来源 | 判定 | 备注 |" in report
    assert "硬机制" in report and "LLM judge" in report
