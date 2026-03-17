"""按题型分派的题目校验测试（TDD 红→绿）。

核心约束：**官方池路径（single/multiple + 官方五维）行为必须逐字节不变**，
新题型（judge/blank/short）走新增分派，个人题（module=个人资料）不校验官方考点骨架。
"""
import pytest

from app.services.validation import validate_question_payload

VALID_KPS = {"教育观", "教师职业道德"}
PERSONAL = "个人资料"


def _official_single(**over):
    d = {
        "module": "职业理念",
        "knowledge_point": "教育观",
        "stem": "下列关于教育观的表述，正确的是？",
        "options": [
            {"key": "A", "text": "正确表述"},
            {"key": "B", "text": "常见误解"},
            {"key": "C", "text": "无关表述"},
            {"key": "D", "text": "颠倒表述"},
        ],
        "answer": ["A"],
        "explanation": "本题考查教育观。",
        "type": "single",
    }
    d.update(over)
    return d


class TestOfficialPathUnchanged:
    """回归保护：官方池路径不得因分派重构而改变。"""

    def test_合法单选通过(self):
        assert validate_question_payload(_official_single(), VALID_KPS) == []

    def test_合法多选通过(self):
        d = _official_single(type="multiple", answer=["A", "B"])
        assert validate_question_payload(d, VALID_KPS) == []

    def test_缺字段(self):
        d = _official_single()
        del d["stem"]
        assert any("stem" in e for e in validate_question_payload(d, VALID_KPS))

    def test_选项键重复(self):
        d = _official_single()
        d["options"][1]["key"] = "A"
        assert any("选项" in e for e in validate_question_payload(d, VALID_KPS))

    def test_答案未命中选项(self):
        d = _official_single(answer=["Z"])
        assert any("答案" in e for e in validate_question_payload(d, VALID_KPS))

    def test_解析为空(self):
        d = _official_single(explanation="  ")
        assert any("解析" in e for e in validate_question_payload(d, VALID_KPS))

    def test_考点不属于官方骨架(self):
        d = _official_single(knowledge_point="不存在的考点")
        assert any("考点" in e for e in validate_question_payload(d, VALID_KPS))

    def test_未知模块(self):
        d = _official_single(module="奇怪模块")
        assert any("未知模块" in e for e in validate_question_payload(d, VALID_KPS))


class TestJudge:
    def _judge(self, **over):
        d = {
            "module": PERSONAL,
            "knowledge_point": "第三章",
            "stem": "教育的本质是培养人的社会活动。",
            "options": [{"key": "A", "text": "正确"}, {"key": "B", "text": "错误"}],
            "answer": ["A"],
            "explanation": "该表述符合资料原文。",
            "type": "judge",
        }
        d.update(over)
        return d

    def test_合法判断通过(self):
        assert validate_question_payload(self._judge(), VALID_KPS) == []

    def test_选项多于两个被拒(self):
        d = self._judge(options=[{"key": "A", "text": "正确"}, {"key": "B", "text": "错误"}, {"key": "C", "text": "不确定"}])
        assert any("2 个选项" in e for e in validate_question_payload(d, VALID_KPS))


class TestBlank:
    def _blank(self, **over):
        d = {
            "module": PERSONAL,
            "knowledge_point": "第三章",
            "stem": "教育的本质是____。",
            "answer": ["培养人的社会活动", "培养人"],
            "explanation": "资料原文如此。",
            "type": "blank",
        }
        d.update(over)
        return d

    def test_合法填空通过_且无需_options(self):
        assert validate_question_payload(self._blank(), VALID_KPS) == []

    def test_答案为空列表被拒(self):
        assert any("answer" in e for e in validate_question_payload(self._blank(answer=[]), VALID_KPS))

    def test_答案项为空串被拒(self):
        assert any("答案项" in e for e in validate_question_payload(self._blank(answer=["", "x"]), VALID_KPS))

    def test_答案重复被拒(self):
        assert any("重复" in e for e in validate_question_payload(self._blank(answer=["甲", "甲"]), VALID_KPS))


class TestShort:
    def _short(self, **over):
        d = {
            "module": PERSONAL,
            "knowledge_point": "第三章",
            "stem": "简述教育的本质。",
            "answer": "教育的本质是培养人的社会活动。",
            "explanation": "评分要点：本质、培养人、社会活动。",
            "type": "short",
        }
        d.update(over)
        return d

    def test_合法简答通过(self):
        assert validate_question_payload(self._short(), VALID_KPS) == []

    def test_参考答案为空被拒(self):
        assert any("参考答案" in e for e in validate_question_payload(self._short(answer="  "), VALID_KPS))


class TestPersonalModule:
    def test_个人题不校验官方考点骨架(self):
        # 考点来自用户资料，不在官方考点集合内也应通过
        d = _official_single(module=PERSONAL, knowledge_point="用户资料里的自定义章节")
        assert validate_question_payload(d, VALID_KPS) == []

    def test_个人题模块值合法(self):
        d = _official_single(module=PERSONAL, knowledge_point="任意")
        errors = validate_question_payload(d, VALID_KPS)
        assert not any("未知模块" in e for e in errors)


class TestUnknownType:
    def test_未知题型按选项题处理(self):
        # 缺省 type 时保持向后兼容（按 single 处理）
        d = _official_single()
        del d["type"]
        assert validate_question_payload(d, VALID_KPS) == []
