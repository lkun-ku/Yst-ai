"""质量闸门 G2（事实一致性）与 G3（答案唯一性投票）。

**为什么单列一组用例**：这两道闸的失败模式都是**静默通过** ——
一道引用了不存在法条的题、一道答案有歧义的题，看起来与正常题毫无区别。
而它们恰好是"AI 出的题不能用"的两个主要原因。所以逐条钉住。

三件事必须被证明：
1. G2 真的扫**题干/解析**（而不只是模型申报的 `source_quote`）——
   否则它与已有的引用校验重复，缺口仍在；
2. **条号必须精确**：`第七十七条` 不能靠库里存在 `第七条` 蒙对；
3. G3 的替身能走通**成功分支**（否则 fake 模式下每条都被拦，链路测不到），
   而改坏答案的题一定被拦。
"""

import json

import pytest

from app.config import settings
from app.services.llm_client import FakeLLMClient, _fake_doc_questions
from app.services.prompts_kb import blind_answer_prompt, parse_blind_answer
from app.services.quality_gates import (
    apply_fact_gate,
    apply_uniqueness_gate,
    check_facts,
    extract_law_refs,
    payload_text,
    vote_once,
    vote_uniqueness,
)

_CHUNKS = [
    {
        "id": 1,
        "content": "第七条 教师享有下列权利：（一）进行教育教学活动。",
        "heading_path": "中华人民共和国教师法 / 第二章 权利和义务 / 第七条",
    },
    {
        "id": 2,
        "content": "第七十七条 教师在教育教学中应当平等对待学生。",
        "heading_path": "中华人民共和国教师法 / 第九章 附则 / 第七十七条",
    },
]


# ---------------- G2：提取与核对 ----------------

def test_提取法条引用_去重保序():
    text = "根据《教师法》第七条，以及《未成年人保护法》第五十九条……另见《教师法》第七条。"
    refs = extract_law_refs(text)
    assert [r.as_text() for r in refs] == ["《教师法》第七条", "《未成年人保护法》第五十九条"]


def test_只有书名号没有条号不算引用():
    """`《综合素质》考纲` 是常见写法，它不是法条引用，不该被 G2 当成待核对项。"""
    assert extract_law_refs("详见《综合素质》考纲与《教育知识与能力》大纲") == []


def test_扫的是题干与解析_不只是申报的引用():
    """这是 G2 存在的理由：模型可能把编造的法条写进**题干**，却不申报为 source_quote。"""
    payload = {"stem": "根据《教师法》第八十七条规定，教师可以自行决定教学内容。",
               "explanation": "本题考查教师权利。", "source_quote": "教师享有下列权利"}
    assert "第八十七条" in payload_text(payload)
    problems = check_facts(payload, _CHUNKS)
    assert problems and "第八十七" in problems[0]


def test_条号必须精确_第七十七条不能靠第七条蒙对():
    """与 `tools.lookup_law` 同一条纪律：条号差一个字，法律含义完全不同。

    库里同时有第七条与第七十七条，所以这里测的是"能否只靠第七条蒙对"。
    """
    only_art7 = [_CHUNKS[0]]
    problems = check_facts({"stem": "依据《教师法》第七十七条，教师应平等对待学生。"}, only_art7)
    assert problems, "只存在第七条时，引用第七十七条必须判为不在依据中"


def test_法名允许简称():
    """模型写「教师法」而语料里是「中华人民共和国教师法」—— 这是正常现象，不该拦。"""
    assert check_facts({"stem": "《教师法》第七条 教师享有下列权利。"}, _CHUNKS) == []


def test_无heading结构时要求正文里出现该条号():
    """个人资料类切片没有「法名 / 章 / 条」结构，退化判据 = 正文里能同时找到法名与条号。"""
    hit = [{"content": "《教师法》第七条 教师享有下列权利", "heading_path": None}]
    assert check_facts({"stem": "《教师法》第七条"}, hit) == []
    # 同类语料里的近邻条号不能蒙对（第-七-十-七-条 不是连续子串）
    near = [{"content": "《教师法》第七十七条 教师应平等对待学生", "heading_path": None}]
    assert check_facts({"stem": "《教师法》第七条"}, near)


def test_个人资料里没提这条法时判为不在依据中():
    """这是 G2 的正确行为而不是误伤：依据里没有这条法，
    说明模型用了**外部知识** —— 而那正是"AI 出的题不能用"的主要来源。"""
    chunks = [{"content": "教师的权利包括教育教学权与研究权。", "heading_path": "讲义 / 第二章"}]
    assert check_facts({"stem": "《教师法》第七条"}, chunks)


def test_G2闸门_拦截并带上原因(monkeypatch):
    monkeypatch.setattr(settings, "gate_g2_enabled", True)
    events = []
    payloads = [
        {"stem": "《教师法》第七条 教师享有下列权利。"},
        {"stem": "《教师法》第八十七条 教师可以自行决定教学。"},
    ]
    kept, blocked = apply_fact_gate(payloads, _CHUNKS, emit=lambda t, s, d=None: events.append((t, s, d)))
    assert len(kept) == 1 and len(blocked) == 1
    assert blocked[0]["_gate"] == "G2" and blocked[0]["_problems"]
    assert events and events[0][0] == "gate_g2"
    assert events[0][2]["blocked"][0]["problems"]


def test_G2可关闭(monkeypatch):
    monkeypatch.setattr(settings, "gate_g2_enabled", False)
    payloads = [{"stem": "《教师法》第八十七条 编的。"}]
    kept, blocked = apply_fact_gate(payloads, _CHUNKS)
    assert kept == payloads and blocked == []


# ---------------- G3：盲答投票 ----------------

def test_盲答提示词里绝不能出现答案():
    """这是 G3 与「生成自检」的本质差别 —— 看到答案就测不出歧义了。"""
    prompt = blind_answer_prompt(
        "下列说法正确的是？", [{"key": "A", "text": "甲"}, {"key": "B", "text": "乙"}], "single"
    )
    assert "C_IS_THE_ANSWER" not in prompt
    assert '"answer"' in prompt  # 只要输出格式，不要答案
    assert "correct" not in prompt.lower()


def test_解析盲答_数组与字符串都收():
    assert parse_blind_answer('{"answer": ["A", "B"]}') == ["A", "B"]
    assert parse_blind_answer('{"answer": "A"}') == ["A"]
    assert parse_blind_answer("不是 JSON") == []


def test_模型不可用时投票无效而不是不一致():
    """一次接口抖动不该把好题判成坏题 —— 无效投票要丢弃，不能算作"不一致"。"""

    class Down:
        def ask(self, prompt, timeout=30):
            return None

    assert vote_once(Down(), {"stem": "x", "options": []}) is None
    r = vote_uniqueness(Down(), {"stem": "x", "options": [], "answer": ["A"]}, n=3)
    assert r.n == 0 and not r.passed


class _StubVoter:
    """按预设答案序列回话的假模型，用来精确构造"一致/不一致/不符"三种情形。"""

    def __init__(self, answers: list[list[str]]) -> None:
        self.answers = list(answers)

    def ask(self, prompt, timeout=30):
        keys = self.answers.pop(0) if self.answers else ["A"]
        return json.dumps({"answer": keys}, ensure_ascii=False)


def test_票数一致且与答案相符才通过():
    payload = {"stem": "x", "options": [], "answer": ["A"], "type": "single"}
    ok = vote_uniqueness(_StubVoter([["A"], ["A"], ["A"]]), payload, n=3)
    assert ok.passed and ok.agree and ok.matches


def test_盲答彼此不一致_判为答案有歧义():
    payload = {"stem": "x", "options": [], "answer": ["A"], "type": "single"}
    r = vote_uniqueness(_StubVoter([["A"], ["B"], ["A"]]), payload, n=3)
    assert r.agree is False and r.passed is False


def test_盲答一致但与题目答案不符_判为答案错误():
    payload = {"stem": "x", "options": [], "answer": ["D"], "type": "single"}
    r = vote_uniqueness(_StubVoter([["A"], ["A"], ["A"]]), payload, n=3)
    assert r.agree is True and r.matches is False and r.passed is False


def test_多选题键序不影响一致性():
    payload = {"stem": "x", "options": [], "answer": ["A", "B"], "type": "multiple"}
    r = vote_uniqueness(_StubVoter([["B", "A"], ["A", "B"], ["B", "A"]]), payload, n=3)
    assert r.passed is True


def test_G3默认关闭(monkeypatch):
    monkeypatch.setattr(settings, "gate_g3_enabled", False)
    payloads = [{"stem": "x", "options": [], "answer": ["A"], "type": "single"}]
    kept, blocked = apply_uniqueness_gate(_StubVoter([]), payloads)
    assert kept == payloads and blocked == []


def test_G3_非选择题型直接放行(monkeypatch):
    """简答/填空没有"唯一答案"这个概念 —— 对它们谈唯一性是概念错误，不是"没通过"。"""
    monkeypatch.setattr(settings, "gate_g3_enabled", True)
    payloads = [
        {"stem": "简答", "type": "short", "answer": "参考答案"},
        {"stem": "填空", "type": "blank", "answer": ["答案"]},
    ]
    kept, blocked = apply_uniqueness_gate(_StubVoter([]), payloads)
    assert len(kept) == 2 and not blocked


def test_G3_命中拦截并写清原因(monkeypatch):
    monkeypatch.setattr(settings, "gate_g3_enabled", True)
    payloads = [{"stem": "有歧义的题", "options": [], "answer": ["A"], "type": "single"}]
    kept, blocked = apply_uniqueness_gate(_StubVoter([["A"], ["B"], ["C"]]), payloads)
    assert not kept and len(blocked) == 1
    assert blocked[0]["_gate"] == "G3"
    assert any("歧义" in x for x in blocked[0]["_problems"])


def test_G3_替身能走通成功分支(monkeypatch):
    """**关键**：若替身答不对，G3 会把 fake 模式下的每道题都拦掉 ——
    那条链路就再也测不到了（与出题链路的引用校验同一个道理）。"""
    monkeypatch.setattr(settings, "gate_g3_enabled", True)
    client = FakeLLMClient()
    for qtype in ("single", "multiple", "judge"):
        payload = _fake_doc_questions("测试考点", qtype, 1)[0]
        r = vote_uniqueness(client, payload, n=2)
        assert r.passed, f"{qtype} 的替身盲答应通过：{r.as_dict()} votes={r.votes}"


def test_G3_改坏答案的题会被拦(monkeypatch):
    """红线：题目答案被改成错的时候，独立的盲答会指向原答案 → 必须拦住。"""
    monkeypatch.setattr(settings, "gate_g3_enabled", True)
    client = FakeLLMClient()
    payload = _fake_doc_questions("测试考点", "single", 1)[0]
    payload["answer"] = ["D"]  # 把正确答案改掉
    kept, blocked = apply_uniqueness_gate(client, [payload])
    assert not kept and blocked and "不符" in "".join(blocked[0]["_problems"])
