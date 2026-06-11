"""AI 出主观题：结构校验、解析、以及"替身能走通成功分支"。

## 为什么结构校验要单列

这道校验是**唯一**能挡住"模型敷衍出一道没法作答的题"的机制（G1 的口径：
只挡结构性缺陷，题目好不好是另一回事）。而它挡不住时的后果很隐蔽：
用户拿到一道 20 字的"题"，然后被批改给出四维度评分 —— 看起来一切正常。

所以逐条钉住三类硬缺陷：题干过短、没有作答指令、材料分析题的材料过短。
"""

from __future__ import annotations

from app.services.llm_client import FakeLLMClient
from app.services.subjective_gen import (
    MIN_STEM_CHARS,
    QTYPE_META,
    generate_subjective,
    parse_subjective,
    structure_issues,
)

_GOOD_MATERIAL = (
    "材料：某中学初二（3）班的李老师在讲《看云识天气》时，没有直接给出结论，"
    "而是先让学生观察窗外的云，再分组记录云的形状与变化，最后请各组说明判断依据；"
    "有学生答错时他不直接否定，而是追问「你是怎么想的」，引导该生自己发现矛盾。"
    "课后他还把学生的记录整理成展板贴在教学楼走廊，让其他班级的同学也能补充；"
    "遇到争议较大的结论，他会让持不同意见的两组各自找证据，下节课再辩一次。"
    "一个学期下来，学生主动提问的次数明显变多，连平时最沉默的几个孩子也开始发言。\n"
    "请从教育观（素质教育观与新课改的教学观）的角度评析李老师的做法。（14 分）"
)


# ---------------- 结构校验 ----------------


def test_过短的题干被判结构不过():
    issues = structure_issues("请谈看法。", "material")
    assert any("过短" in i for i in issues)


def test_没有作答指令的题干被判不过():
    """一段材料不是一道题 —— 考生不知道该答什么，批改也无从判"切题度"。"""
    long_material = "材料：" + "李老师在课堂上让学生自己观察并讨论。" * 12
    issues = structure_issues(long_material, "material")
    assert any("作答指令" in i for i in issues)


def test_材料分析题的材料过短要单独判():
    """材料短到无法支撑"结合材料评析"时，即使字数过了通用下限也不合格。"""
    stem = "材料很短。请从教育观的角度评析。（14 分）" + "补" * MIN_STEM_CHARS
    assert any("材料过短" in i for i in structure_issues(stem, "material"))


def test_合格题干没有结构问题():
    assert structure_issues(_GOOD_MATERIAL, "material") == []


# ---------------- 解析 ----------------


def test_解析能剥围栏并取题干():
    assert parse_subjective('```json\n{"stem": "题目"}\n```') == {"stem": "题目"}


def test_解析失败返回_None_而不是编一个():
    assert parse_subjective("嗯…我觉得可以这样出题") is None
    assert parse_subjective(None) is None
    assert parse_subjective('{"stem": "   "}') is None


# ---------------- 端到端（替身） ----------------


def test_替身能走通成功分支(db_session):
    """替身必须能产出**过得去结构校验**的题 —— 否则这条链路在离线环境里
    永远走不到成功分支，"AI 能出主观题"就无从验证（与其它替身的同一条约定）。"""
    out = generate_subjective(db_session, "material", FakeLLMClient())

    assert "error" not in out, out.get("error")
    assert out["score"] == QTYPE_META["material"]["score"]
    assert len(out["stem"]) >= 200
    assert out["aigc_flag"] is True
    assert "不是真题" in out["note"], "来源必须如实标注，否则用户会当成真题"


def test_不支持的题型返回错误而不是抛异常(db_session):
    out = generate_subjective(db_session, "essay", FakeLLMClient())
    assert "error" in out
