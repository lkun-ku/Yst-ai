"""N3 歧义变体构造的确定性测试（不调模型）。

这些断言守的是**构造手法的正确性**，而构造手法决定了整张表的真值站不站得住：
顶替错了对象、或改动了答案键，下游所有"该不该被拦"的判断就都失真了。
"""

from __future__ import annotations

import pytest

from eval.g3_ambiguity import (
    build_variant,
    clean_paraphrase,
    drop_qualifier,
    mechanical_paraphrase,
    pick_wrong_option,
)

_PAYLOAD = {
    "id": 1,
    "type": "single",
    "stem": "某教师的行为符合规定的是（ ）",
    "options": [
        {"key": "A", "text": "教师应当尊重学生的人格"},
        {"key": "B", "text": "教师可以随意公开学生成绩"},
        {"key": "C", "text": "教师不得参加任何校外活动"},
        {"key": "D", "text": "教师无需接受继续教育"},
    ],
    "answer": ["A"],
}


#: N4 用：题干里带限定词（真实试卷里"主要/根本/最"这类限定被省略是最常见的歧义来源）。
_PAYLOAD_LIMITED = {**_PAYLOAD, "stem": "该校的做法主要违背了素质教育中（ ）"}


def test_只顶替错误选项_正确答案保持原样():
    v = build_variant(_PAYLOAD, "教师应该尊重学生的人格")
    texts = {o["key"]: o["text"] for o in v["options"]}
    assert texts["A"] == "教师应当尊重学生的人格", "正确答案被改动了 —— 那就变成 N1 而不是 N3"
    assert texts["B"] == "教师应该尊重学生的人格"
    assert v["answer"] == ["A"], "答案键不得改动"


def test_选项个数与键不变_只是文本被顶替():
    v = build_variant(_PAYLOAD, "教师应该尊重学生的人格")
    assert [o["key"] for o in v["options"]] == ["A", "B", "C", "D"]
    assert len(v["options"]) == len(_PAYLOAD["options"])


def test_选不出错误选项时返回None而不是造出畸形题():
    """多选题若四个选项全对，就没有任何"可被顶替"的位置 —— 必须跳过而不是硬来。"""
    all_right = {**_PAYLOAD, "answer": ["A", "B", "C", "D"]}
    assert pick_wrong_option(all_right) is None
    assert build_variant(all_right, "任意改写") is None


def test_空改写不得产出变体():
    assert build_variant(_PAYLOAD, "") is None


def test_改写与原句相同时判为无效():
    """原样返回等于没改 —— 若放行，等于把原题当负样本，真值就错了。"""
    same = "教师应当尊重学生的人格"
    assert clean_paraphrase(f"B. {same}", same) == ""
    assert clean_paraphrase(f"“{same}”", same) == ""


def test_清洗模型输出_去掉标号与引号():
    assert clean_paraphrase("改写：教师应该尊重学生的人格", "x") == "教师应该尊重学生的人格"
    assert clean_paraphrase('"教师应该尊重学生的人格"', "x") == "教师应该尊重学生的人格"
    assert clean_paraphrase("\n\n  B、教师应该尊重学生的人格  \n", "x") == "教师应该尊重学生的人格"
    assert clean_paraphrase("", "x") == ""


def test_机械改写是确定性且与原句不同():
    out = mechanical_paraphrase("教师应当尊重学生的人格")
    assert out == "教师应该尊重学生的人格"
    assert out != "教师应当尊重学生的人格"
    # 无处可改 → 明确返回空（宁可跳过，也不用不可靠的改写污染真值）
    assert mechanical_paraphrase("中华人民共和国万岁") == ""
    assert mechanical_paraphrase("") == ""


def test_机械改写只替换一处_避免改过头():
    """一次只动**一组**：替换越多，越可能改出不等价的句子 —— 而等价性是本评测真值的前提。

    所以规则表里排在前面的词会先生效：这里也只有"有权"这一组可用。
    """
    assert mechanical_paraphrase("教师有权参加进修培训") == "教师有权利参加进修培训"


@pytest.mark.parametrize("text", ["", "   ", None])
def test_空输入不抛异常(text):
    assert mechanical_paraphrase(text or "") == ""


# ---------------- N4：题干缺限定 ----------------

def test_去掉限定词_题干变了而答案键与选项未动():
    """N4 改的是**题干**，不是选项也不是答案键 —— 否则就变成了 N1/N3 而不是 N4。"""
    v, word = drop_qualifier(_PAYLOAD_LIMITED)
    assert word == "主要"
    assert "主要" not in v["stem"] and "违背" in v["stem"]
    assert v["options"] == _PAYLOAD_LIMITED["options"]
    assert v["answer"] == _PAYLOAD_LIMITED["answer"]


def test_没有限定词可去时返回None而不是原样返回():
    """原样返回等于没造 —— 会把原题当负样本，真值就错了。"""
    plain = {**_PAYLOAD_LIMITED, "stem": "该校的做法违背了素质教育的要求"}
    v, word = drop_qualifier(plain)
    assert v is None and word == ""


def test_优先去掉更长的限定词():
    """先去「最主要」而不是「最」—— 否则题干会留下「主要」这个残余限定。"""
    v, word = drop_qualifier({**_PAYLOAD_LIMITED, "stem": "该校的做法最主要违背了（ ）"})
    assert word == "最主要" and "最主要" not in v["stem"]
