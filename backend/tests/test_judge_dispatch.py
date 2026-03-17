"""按题型分派的判定测试（TDD）。

红线：single / multiple 的判定行为必须逐字节不变；
新增 blank（文本归一化匹配）与 short（不自动判分，state=self_review）。
"""
import json

from app.models import Module, Question, QuestionType
from app.routers.sessions import _judge

OPTS = [
    {"key": "A", "text": "正确表述"},
    {"key": "B", "text": "错误表述"},
    {"key": "C", "text": "无关表述"},
    {"key": "D", "text": "颠倒表述"},
]


def _q(qtype, answer, options=None, **kw):
    return Question(
        id=kw.get("id", 1),
        module=kw.get("module", Module.PERSONAL),
        knowledge_point=kw.get("knowledge_point", "第三章"),
        stem="题干",
        options=json.dumps(options if options is not None else OPTS, ensure_ascii=False),
        answer=json.dumps(answer, ensure_ascii=False),
        explanation="解析说明",
        type=qtype,
    )


class TestOptionTypesUnchanged:
    def test_单选答对(self):
        j = _judge(_q(QuestionType.SINGLE, ["A"]), ["A"])
        assert j.is_correct is True
        assert j.state == "correct"

    def test_单选答错(self):
        j = _judge(_q(QuestionType.SINGLE, ["A"]), ["B"])
        assert j.is_correct is False
        assert j.state == "wrong"

    def test_多选全对(self):
        j = _judge(_q(QuestionType.MULTIPLE, ["A", "B"]), ["A", "B"])
        assert j.is_correct is True
        assert j.state == "correct"

    def test_多选漏选_partial_且四态含_missed(self):
        j = _judge(_q(QuestionType.MULTIPLE, ["A", "B"]), ["A"])
        assert j.is_correct is False
        assert j.state == "partial"
        assert j.options_state["B"] == "missed"
        assert j.options_state["A"] == "correct_selected"

    def test_多选错选_wrong(self):
        j = _judge(_q(QuestionType.MULTIPLE, ["A", "B"]), ["C"])
        assert j.state == "wrong"


class TestJudge:
    def test_判断题答对(self):
        opts = [{"key": "A", "text": "正确"}, {"key": "B", "text": "错误"}]
        j = _judge(_q(QuestionType.JUDGE, ["A"], options=opts), ["A"])
        assert j.is_correct is True

    def test_判断题答错(self):
        opts = [{"key": "A", "text": "正确"}, {"key": "B", "text": "错误"}]
        j = _judge(_q(QuestionType.JUDGE, ["A"], options=opts), ["B"])
        assert j.is_correct is False


class TestBlank:
    def test_完全命中(self):
        j = _judge(_q(QuestionType.BLANK, ["培养人的社会活动"]), ["培养人的社会活动"])
        assert j.is_correct is True

    def test_可接受变体命中(self):
        j = _judge(_q(QuestionType.BLANK, ["培养人的社会活动", "培养人"]), ["培养人"])
        assert j.is_correct is True

    def test_B2_容错_多余空格(self):
        j = _judge(_q(QuestionType.BLANK, ["培养人的社会活动"]), ["  培养人的社会活动  "])
        assert j.is_correct is True

    def test_B2_容错_全角逗号(self):
        j = _judge(_q(QuestionType.BLANK, ["素质教育"]), ["素质教育，"])
        assert j.is_correct is True

    def test_B2_容错_英文大小写(self):
        j = _judge(_q(QuestionType.BLANK, ["Teacher"]), ["teacher"])
        assert j.is_correct is True

    def test_错误答案(self):
        j = _judge(_q(QuestionType.BLANK, ["培养人的社会活动"]), ["灌输知识"])
        assert j.is_correct is False

    def test_空作答判错(self):
        j = _judge(_q(QuestionType.BLANK, ["培养人的社会活动"]), [""])
        assert j.is_correct is False


class TestShort:
    def test_简答不自动判分(self):
        j = _judge(_q(QuestionType.SHORT, ["参考答案文本"]), ["我的作答"])
        assert j.is_correct is False
        assert j.state == "self_review"

    def test_简答返回参考答案供自评(self):
        j = _judge(_q(QuestionType.SHORT, ["参考答案文本"]), ["我的作答"])
        assert j.correct == ["参考答案文本"]

    def test_简答提示文案引导自评(self):
        j = _judge(_q(QuestionType.SHORT, ["参考答案"]), ["x"])
        assert "自评" in j.positive_note
