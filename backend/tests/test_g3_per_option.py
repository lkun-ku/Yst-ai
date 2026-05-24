"""G3' 逐选项判定的测试（不调模型，用 stub client 灌判定结果）。

守的是三件事：① 提示词**不泄露答案**（这是"盲"的前提）；② 判定聚合规则（多数表决、稳定、
与答案键比对）；③ **并列正确能被看见** —— 这正是新判据存在的理由，旧判据看不见它。
"""

from __future__ import annotations

import pytest

from app.services.prompts_kb import parse_per_option, per_option_prompt
from app.services.quality_gates import vote_per_option

_PAYLOAD = {
    "type": "single",
    "stem": "下列表述正确的是（ ）",
    "options": [
        {"key": "A", "text": "教师应当尊重学生的人格"},
        {"key": "B", "text": "教师可以随意公开成绩"},
        {"key": "C", "text": "教师无需继续教育"},
        {"key": "D", "text": "教师可自行停课"},
    ],
    "answer": ["A"],
}


class _StubClient:
    """按序返回预设的判定响应；`None` 表示模型不可用。"""

    def __init__(self, responses: list[str | None]):
        self._responses = responses
        self.prompts: list[str] = []

    def ask(self, prompt: str, timeout: int = 30) -> str | None:
        self.prompts.append(prompt)
        i = min(len(self.prompts) - 1, len(self._responses) - 1)
        return self._responses[i]


def _verdicts(mapping: dict) -> str:
    import json

    return json.dumps({"verdicts": mapping}, ensure_ascii=False)


def test_提示词不泄露答案():
    p = per_option_prompt(_PAYLOAD["stem"], _PAYLOAD["options"], "single")
    assert "【逐选项判定】" in p
    # 答案键与"正确答案"字样都不能出现 —— 看着答案评对不对，对答案本身的歧义不敏感
    assert '"answer"' not in p
    assert "正确答案" not in p


def test_提示词明确要求不因已有选项成立而否定其它():
    p = per_option_prompt(_PAYLOAD["stem"], _PAYLOAD["options"], "single")
    assert "不要" in p and "多个成立" in p


def test_解析容错_布尔与中文与数字都接受():
    import json

    assert parse_per_option(json.dumps({"verdicts": {"A": True, "B": False}})) == {
        "A": True,
        "B": False,
    }
    assert parse_per_option('{"verdicts": {"A": "是", "B": "否"}}') == {"A": True, "B": False}
    assert parse_per_option('{"verdicts": {"A": 1, "B": 0}}') == {"A": True, "B": False}
    assert parse_per_option("没有 JSON") == {}
    assert parse_per_option("{}") == {}


def test_只有一个选项成立_且与答案键一致_则通过():
    c = _StubClient([_verdicts({"A": True, "B": False, "C": False, "D": False})])
    v = vote_per_option(c, _PAYLOAD, n=3)
    assert v.n == 3 and v.stable is True and v.matches is True
    assert v.judged == ("A",) and v.n_judged == 1
    assert v.passed is True


def test_两个选项都被判成立_即便稳定也要拦截_且能看出并列():
    """**这条就是新判据存在的理由**。

    旧判据在此情形下会"一致地选中某一个"从而通过；新判据把「被判成立的选项」显式暴露出来，
    于是「并列正确」从不可见变成可见。
    """
    c = _StubClient([_verdicts({"A": True, "B": True, "C": False, "D": False})])
    v = vote_per_option(c, _PAYLOAD, n=3)
    assert v.stable is True, "判定本身是完全稳定的 —— 正说明旧判据在这里看不出问题"
    assert v.n_judged == 2 and v.judged == ("A", "B")
    assert v.matches is False and v.passed is False


def test_判定与答案键不符_拦截():
    c = _StubClient([_verdicts({"A": False, "B": True, "C": False, "D": False})])
    v = vote_per_option(c, _PAYLOAD, n=3)
    assert v.matches is False and v.passed is False


def test_多次判定不一致_即便多数与答案相符也要拦截():
    """多数表决会"抹平"抖动，但**稳定性**是独立的失败信号 —— 模型自己拿不准。"""
    c = _StubClient([
        _verdicts({"A": True, "B": False}),
        _verdicts({"A": True, "B": True}),
        _verdicts({"A": True, "B": False}),
    ])
    v = vote_per_option(c, _PAYLOAD, n=3)
    assert v.judged == ("A",), "多数表决后的确是 A"
    assert v.stable is False and v.passed is False


def test_全部判定无效时视为无法判定_而不是未通过():
    """接口抖动 ≠ 题不合格。混同会让欠产飙升（与旧判据同原则）。"""
    c = _StubClient([None])
    v = vote_per_option(c, _PAYLOAD, n=3)
    assert v.n == 0
    assert v.passed is False  # 「未通过」的判定由调用方按 n==0 解释为放行


def test_判无一个选项成立_是有效判定而非无效():
    """模型认为「没有一个选项成立」是**有效**答案（只是与答案键不符），
    不能当成"解析失败"丢掉 —— 否则这类题会被静默放行。
    """
    c = _StubClient([_verdicts({"A": False, "B": False, "C": False, "D": False})])
    v = vote_per_option(c, _PAYLOAD, n=3)
    assert v.n == 3
    assert v.judged == () and v.matches is False and v.passed is False


@pytest.mark.parametrize("votes", [1, 2, 5])
def test_多数表决在各种投票数下都取过半(votes):
    c = _StubClient([_verdicts({"A": True, "B": False})])
    v = vote_per_option(c, _PAYLOAD, n=votes)
    assert v.n == votes and v.judged == ("A",)
