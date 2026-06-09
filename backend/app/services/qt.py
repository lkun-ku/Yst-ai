"""题型清单的**唯一真相**。

## 为什么要有这个文件（2026-06-15 实测踩到）

`routers/marking.py` 与 `routers/questions.py` 各写了一份题型元组：

- `marking.QTYPES = ("material", "writing", "short", "design", "default")`
- `questions.PRACTICE_QTYPES = ("material", "writing", "short", "design")`

后者少一个 `default`，而小程序的「其它主观题」标签**恰好发 `default`** ——
于是那个标签点「抽题」必然吃 **400「不支持练习的题型：default」**。
`questions.py` 的注释当时就写了"两边若各写一份会悄悄分叉" —— 这次就是那个分叉。

⚠️ **两份清单分叉的代价不对称**：抽题侧多拒一个题型，用户看到的是"这个模块坏了"，
而**没有任何日志或测试会红**。所以这里收口成一份，并加测试断言两边一致。
"""

from __future__ import annotations

#: 有独立判分口径、且**可以从题库抽**的主观题型。
#: `design`（教学设计）在科目一是独立题型，`short`（简答）在科目二是主力题型。
SUBJECTIVE_QTYPES: tuple[str, ...] = ("material", "writing", "short", "design")

#: 批改侧额外接受 `default`：用户**自己粘贴题目**时可能说不清题型，
#: 这时判分走通用维度（`marking.DIMENSION_LABELS` 的通用档），不该拒绝他。
#: ⚠️ 但它**不是**一个可抽题的题型 —— 抽题侧遇到 `default` 的语义是
#: 「随便给我一道主观题」，见 `routers/questions.py`。
DEFAULT_QTYPE = "default"

MARKING_QTYPES: tuple[str, ...] = SUBJECTIVE_QTYPES + (DEFAULT_QTYPE,)
