"""引用闸门在**两类语料**上的成对指标（把基准数字变成常驻断言）。

**为什么要把基准写进测试**：ADR-0021 登记的边界是"非条文语料的闸门行为只有单元级证据、
没有基准级数字" —— 单元用例只能证明"某一条能/不能过"，证明不了**成对结论**
（拦截率 > 0 且流出编造率 = 0）。基准脚本能给出这组数字，但它**要人去跑**才存在；
写进测试之后，任何让闸门退化的改动都会立刻变红。

同时锁住一件容易被"重构掉"的事：**反例类别名是按语料生成的**
（法条「换条号」/ 考纲「换考点」）—— 若有人把它统一成一个中性名，
法条的历史基准就无法逐行对比了。
"""
import pytest

from eval.citation_eval import CORPORA, _verify, build_cases, load_chunks
from eval.metrics import evaluate_citation_gate


def _run(corpus: str):
    spec = CORPORA[corpus]
    chunks = load_chunks(spec)
    cases, stats = build_cases(chunks, spec)
    return spec, chunks, cases, stats, evaluate_citation_gate(cases, _verify)


@pytest.mark.parametrize("corpus", sorted(CORPORA))
def test_两类语料的闸门都是成对达标(corpus: str):
    """`sound` = 拦截率 > 0 **且** 流出编造率 = 0。

    只看后者是自欺 —— 闸门未触发时它同样是 0。
    """
    _spec, chunks, cases, _stats, m = _run(corpus)
    assert chunks, f"{corpus} 语料为空 —— 基准会退化成 0 用例而静默通过"
    assert cases, f"{corpus} 没生成任何用例"
    assert m.sound, f"{corpus} 成对结论未达标：{m.as_row()}"
    assert m.interception_rate == 1.0, f"{corpus} 有漏放：{m.as_row()}"
    assert m.leaked_fabrication_rate == 0.0, f"{corpus} 有编造流出：{m.as_row()}"
    assert m.false_negative_rate == 0.0, f"{corpus} 有误杀：{m.as_row()}"


def test_反例类别名按语料生成():
    """法条「换条号」与考纲「换考点」是同一类错误的两种称呼。

    类别名必须**跟着语料变**：统一成"换标签"会让法条的历史基准失去可比性，
    而考纲上写"换条号"更是无中生有（考纲没有条）。
    """
    _s1, _c1, law_cases, _st1, _m1 = _run("laws")
    _s2, _c2, syl_cases, _st2, _m2 = _run("syllabus")
    law_kinds = {c["kind"] for c in law_cases}
    syl_kinds = {c["kind"] for c in syl_cases}
    assert "换条号" in law_kinds and "换条号" not in syl_kinds
    assert "换考点" in syl_kinds and "换考点" not in law_kinds
    # 两边的"拼接"量词也不同（条 / 段）
    assert "拼接两条" in law_kinds and "拼接两段" in syl_kinds


def test_反例真值仍用精确子串缺席而不依赖校验器():
    """这条是基准可信度的地基：若反例真值改用校验器自己的归一化判定，
    就等于让被测对象自己出考卷 —— 漏放率会恒为 0，测试失去意义。

    可验证的表现：反例在语料里**精确不存在**，且**确实与原文不同**（不会把正例误判成反例）。
    """
    from eval.citation_eval import _absent_exact, _content_changed

    _spec, chunks, cases, _stats, _m = _run("syllabus")
    corpus = [c.get("content") or "" for c in chunks]
    negatives = [c for c in cases if c["fabricated"]]
    assert negatives
    for c in negatives:
        assert _absent_exact(c["quote"], corpus), f"反例其实在语料里存在：{c['quote'][:50]!r}"
    # 内容确实被改动过（凭空编造无原文可比，故只对有原文的那类检查）
    assert _content_changed("理解国家实施素质教育的基本要求。", "理解国家实施素质教育的要求。")
    assert not _content_changed("理解要求。", "理解要求。")
