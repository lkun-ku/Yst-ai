"""领域包（Domain Pack）：官方口径断言 + 四条不变量。

这些用例守的不是「函数能跑」，而是**口径**：

1. 科目二**名称随学段变化**（保教 / 教育教学 / 教育知识）——
   旧的单一 `EDU_KNOWLEDGE` 是错误数据（`docs/用户需求文档.md` §5 D3，🔴 致命）；
2. 综合素质**分卷命题** 101 / 201 / 301，不是「各学段通考」（§5 D2，🔴 致命）；
3. **「官方有、本仓未实现」必须与「官方不存在」可区分** ——
   否则后来者会把「还没做」误读成「官方没有这个东西」；
4. 配置错必须在**加载期**就报错，而不是静默用默认值 ——
   四条不变量各有一条用例守着；用一个临时目录造出「错的包」，验证它确实会被拦下。
"""

import pytest

from app.domain_packs import (
    DomainPackError,
    load_all,
    load_pack,
    missing_packs,
    module_weights,
    pack_of,
)
from app.models import (
    OFFICIAL_SLOTS,
    PAPER_CODES,
    SUBJECT_META,
    Module,
    QuestionType,
    Stage,
    Subject,
    paper_code_of,
    subject_of,
)


# ---------------- 官方口径（§1.1 / §1.3 / §1.5）----------------

def test_科目一一个包覆盖三个学段():
    """综合素质「各学段均考、分学段命题」→ 一个包 + 学段标签，而不是拆三个包。"""
    pack = pack_of(Subject.K1_COMPREHENSIVE)
    assert (pack.no, pack.name) == (1, "综合素质")
    assert pack.stages == (Stage.KINDERGARTEN, Stage.PRIMARY, Stage.MIDDLE)


def test_综合素质分卷命题代码_101_201_301():
    """D2：分学段命题，代码不同 —— 这是「题目必须带学段标签」的官方依据。"""
    assert paper_code_of(Subject.K1_COMPREHENSIVE, Stage.KINDERGARTEN) == "101"
    assert paper_code_of(Subject.K1_COMPREHENSIVE, Stage.PRIMARY) == "201"
    assert paper_code_of(Subject.K1_COMPREHENSIVE, Stage.MIDDLE) == "301"


def test_科目二中学版本的名称与代码():
    """D3：科目二名称随学段变化；本仓实现的是中学版本。"""
    pack = pack_of(Subject.K2_MIDDLE)
    assert (pack.no, pack.name) == (2, "教育知识与能力")
    assert pack.stages == (Stage.MIDDLE,)
    assert paper_code_of(Subject.K2_MIDDLE, Stage.MIDDLE) == "302"


def test_每门科目的试卷代码覆盖其全部学段():
    """`SUBJECT_META.stages` 与 `PAPER_CODES` 必须一一对应 ——
    少一个学段就会出现「有学段、没代码」，组卷时无从标注。"""
    for subject, meta in SUBJECT_META.items():
        assert set(PAPER_CODES[subject]) == set(meta.stages), subject


def test_科目一五模块权重之和为_1():
    pack = pack_of(Subject.K1_COMPREHENSIVE)
    assert pack.has_weights
    assert abs(pack.weight_sum() - 1.0) < 1e-3


def test_科目一权重可映射回_Module_枚举且顺序与考纲一致():
    weights = module_weights(Subject.K1_COMPREHENSIVE)
    assert [m for m, _ in weights] == [
        Module.PROFESSIONAL_IDEA,
        Module.PROFESSIONAL_ETHICS,
        Module.EDU_LAW,
        Module.CULTURE_LITERACY,
        Module.BASIC_ABILITY,
    ]
    assert abs(sum(w for _, w in weights) - 1.0) < 1e-3


def test_科目二未标权重时必须明确报错而不是返回空():
    """官方未公布科目二模块权重（§1.2 只给模块名）→ 不猜、不用平均值兜底。
    若这里静默返回空列表，组卷会以为「没有权重可用」而悄悄退化成平均分配。"""
    with pytest.raises(DomainPackError):
        module_weights(Subject.K2_MIDDLE)


def test_主观题三型已进入_QuestionType_枚举():
    """材料分析 42 + 写作 50 = 61% 卷面 —— 批改要按题型分流 rubric，题型必须先存在。"""
    values = {t.value for t in QuestionType}
    assert {"material", "writing", "design"} <= values


def test_每条声明题型都是_QuestionType_成员():
    valid = {t.name for t in QuestionType}
    for key, pack in load_all().items():
        assert set(pack.question_types) <= valid, key


def test_load_all_覆盖全部_Subject_成员():
    """枚举与目录必须一一对应 —— 多一个目录是「幽灵包」，少一个目录是「有科目没内容」。"""
    assert set(load_all()) == {s.value for s in Subject}


# ---------------- 缺口语义（未实现 ≠ 不存在）----------------

def test_官方槽位与实际实现的差值即为缺口():
    implemented = {(meta.no, stage) for meta in SUBJECT_META.values() for stage in meta.stages}
    assert len(OFFICIAL_SLOTS) == 6  # §1.1 全表
    assert len(implemented) == 4  # 科目一 × 3 学段 + 科目二 × 中学
    assert len(missing_packs()) == 2


def test_缺口里是未实现的科目二版本():
    gaps = missing_packs()
    assert (2, "保教知识与能力", Stage.KINDERGARTEN) in gaps
    assert (2, "教育教学知识与能力", Stage.PRIMARY) in gaps
    # 已实现的中学版本不该出现在缺口里
    assert (2, "教育知识与能力", Stage.MIDDLE) not in gaps


def test_未实现的组合返回_None_而不是抛错():
    """官方存在但本仓未实现 → None（调用方可据此提示「暂未开放」）；
    官方根本不存在的组合，调用方应先由报考规则排除，不由本函数兼任。"""
    assert subject_of(1, Stage.PRIMARY) is Subject.K1_COMPREHENSIVE
    assert subject_of(2, Stage.PRIMARY) is None
    assert paper_code_of(Subject.K2_MIDDLE, Stage.KINDERGARTEN) is None


# ---------------- 四条不变量（用临时目录造「错的包」，验证确实会被拦下）----------------

def _write_pack(tmp_path, dirname: str, body: str):
    pack_dir = tmp_path / dirname
    pack_dir.mkdir()
    (pack_dir / "pack.yaml").write_text(body, encoding="utf-8")
    return pack_dir


_VALID_BODY = """
key: k1_comprehensive
version: "test"
modules:
  - enum: PROFESSIONAL_IDEA
    name: 职业理念
    weight: 1.0
question_types: [SINGLE]
"""


def test_不变量1_目录名与_key_不一致必须报错(tmp_path):
    pack_dir = _write_pack(tmp_path, "wrong_dir_name", _VALID_BODY)
    with pytest.raises(DomainPackError, match="不变量 1"):
        load_pack(pack_dir)


def test_不变量2_key_不在_Subject_枚举必须报错(tmp_path):
    body = _VALID_BODY.replace("k1_comprehensive", "k9_unknown")
    pack_dir = _write_pack(tmp_path, "k9_unknown", body)
    with pytest.raises(DomainPackError, match="不变量 2"):
        load_pack(pack_dir)


def test_不变量3_题型写错必须报错(tmp_path):
    """写错一个题型名（如 materials）会让组卷静默漏掉该题型。"""
    body = _VALID_BODY.replace("question_types: [SINGLE]", "question_types: [materials]")
    pack_dir = _write_pack(tmp_path, "k1_comprehensive", body)
    with pytest.raises(DomainPackError, match="不变量 3"):
        load_pack(pack_dir)


def test_不变量4_权重和不为1_必须报错(tmp_path):
    body = _VALID_BODY.replace("weight: 1.0", "weight: 0.9")
    pack_dir = _write_pack(tmp_path, "k1_comprehensive", body)
    with pytest.raises(DomainPackError, match="不变量 4"):
        load_pack(pack_dir)


def test_未声明权重时跳过求和校验(tmp_path):
    """只要有一个模块没写 weight，本包即整体视为未标权重 —— 不混合、不补默认值。
    这正是科目二的情形（官方未公布权重），必须能正常加载。"""
    body = _VALID_BODY.replace("    weight: 1.0\n", "")
    pack_dir = _write_pack(tmp_path, "k1_comprehensive", body)
    pack = load_pack(pack_dir)
    assert pack.has_weights is False
    assert pack.weight_sum() == 0.0


def test_模块_enum_写错必须报错(tmp_path):
    body = _VALID_BODY.replace("PROFESSIONAL_IDEA", "NOT_A_MODULE")
    pack_dir = _write_pack(tmp_path, "k1_comprehensive", body)
    with pytest.raises(DomainPackError, match="不是 Module 成员"):
        load_pack(pack_dir)


def test_权重越界必须报错(tmp_path):
    body = _VALID_BODY.replace("weight: 1.0", "weight: 1.5")
    pack_dir = _write_pack(tmp_path, "k1_comprehensive", body)
    with pytest.raises(DomainPackError, match=r"\(0, 1\]"):
        load_pack(pack_dir)


def test_缺少_pack_yaml_必须报错(tmp_path):
    empty_dir = tmp_path / "k1_comprehensive"
    empty_dir.mkdir()
    with pytest.raises(DomainPackError, match="缺少 pack.yaml"):
        load_pack(empty_dir)
