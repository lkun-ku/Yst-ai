"""题型清单只能有一份 —— 这条测试来自一次实测故障（2026-06-15）。

## 故障

小程序「其它主观题」标签点「抽题」必然失败，控制台是
`GET /api/questions/practice?qtype=default → 400 不支持练习的题型：default`。

根因不是数据、不是模型，而是**两份题型清单分叉**：

- `routers/marking.py`：`("material","writing","short","design","default")`
- `routers/questions.py`：`("material","writing","short","design")`

后者少一个 `default`，而页面恰好发它。`questions.py` 当时的注释已经写了
"两边若各写一份会悄悄分叉" —— 这就是那个分叉，只是没人拦它。

## 为什么值得单列一组

这类分叉的**代价不对称**：抽题侧少认一个题型，用户看到的是"这个模块坏了"，
而没有任何日志、没有测试会红。所以这里既钉"清单同源"，也钉"`default` 的语义"。
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.routers import marking as marking_router
from app.routers import questions as questions_router
from app.services.qt import DEFAULT_QTYPE, MARKING_QTYPES, SUBJECTIVE_QTYPES


def test_两个路由引用同一份清单():
    assert questions_router.PRACTICE_QTYPES == SUBJECTIVE_QTYPES
    assert marking_router.QTYPES == MARKING_QTYPES
    assert set(SUBJECTIVE_QTYPES) <= set(MARKING_QTYPES), "可抽题的题型必须都能批改"


def test_批改比抽题多一个default():
    """`default` 是**批改侧**的兜底题型（用户粘题说不清题型时走通用维度），
    它不是一个能从题库抽的题型 —— 但抽题侧**必须认识**这个词（语义=任意主观题）。"""
    assert DEFAULT_QTYPE in MARKING_QTYPES
    assert DEFAULT_QTYPE not in SUBJECTIVE_QTYPES
    assert questions_router.ANY_SUBJECTIVE == DEFAULT_QTYPE


def test_default_不再报400(db_session):
    """`default` 要么抽到题，要么明确 404（题库真空）；**绝不能是 400**。

    库内容随别的用例变化，所以两种结果都接受 —— 唯一不许的是"参数被拒"。
    """
    try:
        out = questions_router.practice_question(qtype=DEFAULT_QTYPE, _c=object(), db=db_session)
        assert out.qtype in SUBJECTIVE_QTYPES, "抽到题时必须是主观题型之一"
    except HTTPException as e:
        assert e.status_code == 404, f"default 被判为非法题型（400）→ 页面那个标签必然坏：{e.detail}"


def test_非法题型仍然要报400(db_session):
    """放宽不等于不校验：真正不存在的题型该拒还得拒。"""
    with pytest.raises(HTTPException) as ei:
        questions_router.practice_question(qtype="essay", _c=object(), db=db_session)
    assert ei.value.status_code == 400
