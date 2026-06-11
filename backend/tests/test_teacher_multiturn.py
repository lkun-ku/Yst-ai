"""问答老师的**多轮**：指代补全（检索改写）与历史裁剪。

## 为什么"改写"是多轮能不能用的关键

检索发生在**作答之前**，它只看得到查询串。而多轮的第二句往往是
「第三条呢」「那它要多久」这类**指代** —— 在检索层没有任何词可匹配。
拿它直接去检索，返回的是"凑数的 top-k"，而**看起来一切正常**（有结果、有引用），
只是全不相干。这是多轮最典型的静默失效：错得不响。

所以这里钉两件事：**多轮必须改写**、**改写失败必须回退原问题**（绝不能把查询变成空的，
那会把"能答"变成"拒答"，而且同样不报错）。
"""

from __future__ import annotations

import json

from app.services.llm_client import FakeLLMClient
from app.services.scope import NAMESPACE_PERSONAL, Scope
from app.services.teacher_agent import (
    MAX_HISTORY_CHARS,
    MAX_HISTORY_TURNS,
    _retrieval_query,
    _trim_history,
    ask,
)


class _RewriteStub:
    """指定改写结果（或让它失败）的假模型，并记下收到的提示词。"""

    def __init__(self, result: str | None) -> None:
        self.result = result
        self.prompts: list[str] = []

    def ask(self, prompt, timeout=30):
        self.prompts.append(prompt)
        return self.result


_HISTORY = [
    {"role": "user", "content": "未成年人保护法里学校保护有哪些条文？"},
    {"role": "ai", "content": "资料库里有第三十五条、第四十条、第四十一条。"},
]


# ---------------- 检索查询改写 ----------------


def test_单轮不做改写调用():
    """单轮的查询就是原问题 —— 不能白花一次调用。"""
    stub = _RewriteStub("不该被调用")
    out = _retrieval_query({"question": "教师法第七条", "client": stub})
    assert out == "教师法第七条"
    assert stub.prompts == []


def test_多轮把指代补全成可独立检索的查询():
    stub = _RewriteStub("未成年人保护法 第三章 学校保护 有哪些条文")
    out = _retrieval_query({"question": "第三条呢", "history": _HISTORY, "client": stub})

    assert out == "未成年人保护法 第三章 学校保护 有哪些条文"
    assert len(stub.prompts) == 1
    # 历史必须真的进了提示词，否则模型无从知道"第三条"指什么
    assert "未成年人保护法里学校保护有哪些条文？" in stub.prompts[0]
    assert "第三条呢" in stub.prompts[0]


def test_改写失败必须回退原问题而不是空查询():
    """**最关键的一条安全网**：改写是"优化"，失败了不能把检索搞崩。

    空查询的后果不是报错，而是**静默变成拒答** —— 与"资料库里确实没有"表现一样，
    排查时会被当成数据问题。
    """
    for broken in ("", "   ", None):
        stub = _RewriteStub(broken)
        out = _retrieval_query({"question": "第三条呢", "history": _HISTORY, "client": stub})
        assert out == "第三条呢"


def test_改写抛异常也回退原问题():
    class Boom:
        def ask(self, prompt, timeout=30):
            raise RuntimeError("接口挂了")

    out = _retrieval_query({"question": "第三条呢", "history": _HISTORY, "client": Boom()})
    assert out == "第三条呢"


# ---------------- 历史裁剪 ----------------


def test_历史只保留最近若干轮():
    long_hist = [{"role": "user", "content": f"第{i}问"} for i in range(20)]
    out = _trim_history(long_hist)
    assert len(out) == MAX_HISTORY_TURNS
    assert out[-1]["content"] == "第19问"


def test_单条过长会被裁掉():
    out = _trim_history([{"role": "user", "content": "啊" * 5000}])
    assert len(out[0]["content"]) == MAX_HISTORY_CHARS


def test_角色与空内容被归一():
    out = _trim_history([
        {"role": "assistant", "content": "AI 的话"},   # 非 user 一律记作 ai
        {"role": "user", "content": "   "},            # 空内容丢弃
        {"role": "user", "content": "真问题"},
    ])
    assert out == [{"role": "ai", "content": "AI 的话"}, {"role": "user", "content": "真问题"}]


# ---------------- 端到端（替身） ----------------


def test_多轮提问能走通并回传实际检索查询(db_session):
    """替身下改写是恒等变换 —— 所以 `retrieval_query` 应等于原问题。

    ⚠️ 这里刻意断言"等于原问题"而不是"等于某个改写值"：替身**不编造**改写结果，
    于是被验证的是链路（历史进了提示词、查询确实被用来检索），不是替身的想象力。
    """
    empty = Scope(namespace=NAMESPACE_PERSONAL, candidate_id=987655)
    r = ask(
        db_session, 987655, "第三条呢", empty,
        mode="grounded", client=FakeLLMClient(), history=_HISTORY,
    )
    assert r["retrieval_query"] == "第三条呢"
    assert isinstance(r["answer"], str | type(None))


def test_不传历史时行为与单轮一致(db_session):
    empty = Scope(namespace=NAMESPACE_PERSONAL, candidate_id=987656)
    r = ask(db_session, 987656, "教师法第七条", empty, mode="grounded", client=FakeLLMClient())
    assert r["retrieval_query"] == "教师法第七条"
