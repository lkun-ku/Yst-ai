"""问答老师：工具层契约、三档模式、以及两条"必须拦住"的红线。

**为什么单列一组用例**：这是本项目第一条**多步自主决策**的链路
（模型自己决定查什么、查几次），也是第一条要同时满足"能答"和"敢拒答"的链路。
它有四种失败模式都不报错，只会安静地给出坏结果：

1. 工具失败被当成异常抛出 → 整个问答崩（应当降级为"用已有材料作答"）；
2. 条号模糊匹配 → 拿「第七十七条」当「第七条」用（**编造条文的一种形态**）；
3. 引用无法定位却照常输出 → "句句有出处"变成口号；
4. 没有证据也硬答 → 拒答这条出口被绕过。

所以这里逐条钉住，尤其是 `lookup_law` 的精确复核与 `verify` 的硬拦截。
"""

import json

import pytest

from app.models import Candidate, Document, DocumentChunk
#: **必须显式注入假客户端**：本仓的 conftest 刻意不强制 `LLM_MODE=fake`
#: （见其注释：真实模型下的出题/判分才可信）。于是"不传 client"的用例会去打真实接口，
#: 在没有 key 或网络不通的机器上行为完全不同 —— 那样的用例是环境依赖，不是测试。
from app.services.llm_client import FakeLLMClient
from app.services.scope import NAMESPACE_OFFICIAL, NAMESPACE_PERSONAL, Scope
from app.services.teacher_agent import _n_verify, ask
from app.services.tools import (
    CHECK_QUOTE,
    LOOKUP_LAW,
    SEARCH_KB,
    TOOLS,
    ToolContext,
    decide,
    execute,
    tool_blocks,
    validate_args,
)

# 教师法第七条（正文含条号，与 kb_corpus 的切分一致）
_ART7 = "第七条 教师享有下列权利：（一）进行教育教学活动，开展教育教学改革和实验；"
_ART77 = "第七十七条 教师在教育教学中应当平等对待学生，关注学生的个体差异。"

#: **法名必须是本文件独有的**。第一版用的是「中华人民共和国教师法」，
#: 但别的用例会灌入**真实的**法条语料（含《教师法》第七条）——
#: 于是 `like %教师法%` 同时命中两份，条数断言随机失败。
#: 共享一份会话级测试库时，测试数据必须自带唯一标识。
_TEST_LAW = "测试示例法"


#: 本文件专用语料的标识。用它做删除范围，**只清自己那份** ——
#: 清空"全部官方语料"会动到别的用例依赖的共享数据（第一版就是这么写的，
#: 结果既破坏隔离、又让全量耗时从 28s 涨到 140s）。
_TEST_STORAGE_PATH = "laws/__test_teacher__.md"


def _seed_official(db, rows: list[tuple[str, str]]) -> None:
    """建一份官方语料：rows = [(heading_path, content)]。

    ⚠️ **必须先清掉本文件上次留下的那份**：本仓的测试库是**会话级**重建
    （`conftest._reset_db` 是 session 作用域），**不逐用例回滚** ——
    否则同一文件里每个用例都会往同一份库里追加，条号查询越查越多条。
    （这个坑让 `test_按条号精确取条文` 一开始返回了 3 条而不是 1 条。）
    """
    old = [r[0] for r in db.query(Document.id).filter(
        Document.storage_path == _TEST_STORAGE_PATH).all()]
    if old:
        db.query(DocumentChunk).filter(DocumentChunk.document_id.in_(old)).delete(
            synchronize_session=False
        )
        db.query(Document).filter(Document.id.in_(old)).delete(synchronize_session=False)
        db.commit()

    doc = Document(
        candidate_id=None,
        is_official=True,
        title=_TEST_LAW,
        file_type="md",
        char_count=sum(len(c) for _h, c in rows),
        chunk_count=len(rows),
        status="parsed",
        storage_path=_TEST_STORAGE_PATH,
    )
    db.add(doc)
    db.flush()
    for seq, (heading, content) in enumerate(rows):
        db.add(
            DocumentChunk(
                document_id=doc.id,
                seq=seq,
                content=content,
                heading_path=heading,
                char_count=len(content),
            )
        )
    db.commit()


_OFFICIAL_ROWS = [
    (f"{_TEST_LAW} / 第二章 权利和义务 / 第七条", _ART7),
    (f"{_TEST_LAW} / 第九章 附则 / 第七十七条", _ART77),
]


@pytest.fixture
def official(db_session):
    _seed_official(db_session, _OFFICIAL_ROWS)
    return Scope(namespace=NAMESPACE_OFFICIAL)


def _ctx(db, scope) -> ToolContext:
    return ToolContext(db, scope, candidate_id=0)


# ---------------- 工具层：参数校验与降级 ----------------

def test_参数校验_必填_类型_枚举_长度():
    assert validate_args(SEARCH_KB, {"query": "教育"}) == []
    assert "缺少必填参数 query" in validate_args(SEARCH_KB, {})[0]
    assert "应为整数" in validate_args(SEARCH_KB, {"query": "x", "k": "3"})[0]
    assert "过长" in validate_args(SEARCH_KB, {"query": "x" * 500})[0]
    assert validate_args(SEARCH_KB, "not-a-dict") == ["args 必须是对象"]


def test_多余参数忽略而不是报错(db_session, official):
    """模型偶尔多带一个键，不该因此算失败 —— 那会白扔一次调用。"""
    out = execute(SEARCH_KB, {"query": "教师", "k": 2, "没用的键": 1}, _ctx(db_session, official))
    assert out["ok"] is True


def test_工具内部报错不抛异常_而是返回失败态():
    class Boom:
        pass

    spec = SEARCH_KB.__class__(
        name="boom", description="", parameters={}, handler=lambda **k: 1 / 0
    )
    out = execute(spec, {}, _ctx(None, None))
    assert out["ok"] is False and "ZeroDivisionError" in out["error"]


def test_未知工具被当成失败观察(db_session, official):
    """模型可能选一个不存在的工具 —— 要让它看到失败并改口，而不是崩掉。"""
    from app.services.teacher_agent import _run_tool

    res = _run_tool({"db": db_session, "scope": official, "candidate_id": 0}, "no_such_tool", {})
    assert res["ok"] is False and "未知工具" in res["error"]


# ---------------- lookup_law：本次最关键的一条 ----------------

def test_按条号精确取条文(db_session, official):
    out = execute(LOOKUP_LAW, {"law": _TEST_LAW, "article": "第七条"}, _ctx(db_session, official))
    assert out["ok"] is True and len(out["items"]) == 1
    assert out["items"][0]["content"].startswith("第七条 ")
    assert out["items"][0]["heading_path"].endswith("第七条")


def test_不把第七十七条当成第七条(db_session, official):
    """`LIKE %第七条%` 会命中「第七十七条」—— 这正是**编造条文**的一种形态。

    条号差一个字，法律含义完全不同。所以 SQL 粗筛之后**必须在应用层复核条号段**，
    否则工具会自信地返回错误的条文，而调用方无从分辨。
    """
    out = execute(LOOKUP_LAW, {"law": _TEST_LAW, "article": "第七条"}, _ctx(db_session, official))
    contents = [i["content"] for i in out["items"]]
    assert all("第七十七条" not in c for c in contents)
    assert not any(i["heading_path"].endswith("第七十七条") for i in out["items"])


def test_条号不存在时如实返回空_而不是退回模糊命中(db_session, official):
    out = execute(LOOKUP_LAW, {"law": _TEST_LAW, "article": "第九十九条"}, _ctx(db_session, official))
    assert out["items"] == []
    assert "未找到" in out["note"]


def test_只给法名时按法名过滤(db_session, official):
    out = execute(LOOKUP_LAW, {"law": _TEST_LAW}, _ctx(db_session, official))
    assert len(out["items"]) == 2


def test_lookup_law_不越权到个人资料(db_session):
    """工具与检索层用同一套命名空间条件 —— 工具不能成为绕过权限的后门。"""
    other = Candidate(id=9911, unionid="tool9911")
    db_session.add(other)
    doc = Document(
        candidate_id=9911, is_official=False, title="别人的讲义", file_type="txt",
        char_count=10, chunk_count=1, status="parsed", storage_path="别人的讲义",
    )
    db_session.add(doc)
    db_session.flush()
    db_session.add(DocumentChunk(document_id=doc.id, seq=0, content="第七条 私人笔记",
                                 heading_path="别人的讲义 / 第七条", char_count=8))
    db_session.commit()

    scope = Scope(namespace=NAMESPACE_PERSONAL, candidate_id=12345)  # 另一个考生
    out = execute(LOOKUP_LAW, {"law": "讲义", "article": "第七条"}, _ctx(db_session, scope))
    assert out["items"] == []


# ---------------- check_quote：让模型能主动自检 ----------------

def test_引用自检_命中与未命中(db_session, official):
    hit = execute(CHECK_QUOTE, {"quote": "教师享有下列权利"}, _ctx(db_session, official))
    assert hit["ok"] is True and hit["items"]
    miss = execute(CHECK_QUOTE, {"quote": "教师有权自行决定教学内容"}, _ctx(db_session, official))
    assert miss["items"] == [] and "未找到" in miss["text"]


def test_工具说明包含三个工具名():
    blocks = tool_blocks()
    assert all(t.name in blocks for t in TOOLS)
    assert len(TOOLS) == 3


# ---------------- 决策解析的容错 ----------------

def test_决策解析失败返回None而不是抛异常():
    class Bad:
        def ask(self, prompt, timeout=30):
            return "我觉得应该先查一下"  # 不是 JSON

    assert decide(Bad(), "问题", [], 4) is None


def test_决策解析_非字典args被兜住():
    class Odd:
        def ask(self, prompt, timeout=30):
            return '{"tool": "search_kb", "args": "教师法"}'

    d = decide(Odd(), "问题", [], 4)
    assert d["tool"] == "search_kb" and d["args"] == {}


# ---------------- 图：三档模式与两条红线 ----------------

class _StubClient:
    """可编排的假客户端：按顺序吐出决策与回答。"""

    def __init__(self, decisions: list[str], answer: str) -> None:
        self.decisions = list(decisions)
        self.answer = answer
        self.answer_calls = 0

    def ask(self, prompt, timeout=30):
        if "【工具决策】" in prompt:
            return self.decisions.pop(0) if self.decisions else '{"tool": "answer"}'
        self.answer_calls += 1
        return self.answer


def test_无据可依时拒答(db_session, official):
    """没有任何证据 → 拒答。这是"查不到就不要编"的出口，必须留着。"""
    # 让检索什么都查不到：把 scope 指向一个空的个人库
    empty = Scope(namespace=NAMESPACE_PERSONAL, candidate_id=987654)
    r = ask(db_session, 987654, "随便问问", empty, mode="grounded", client=FakeLLMClient())
    assert r["refused"] is True
    assert r["answer"] is None
    assert "没有" in r["refusal_reason"] or "无据" in r["refusal_reason"]


def test_有据时作答且引用校验通过(db_session, official):
    r = ask(db_session, 0, "教师法第七条", official, mode="grounded", client=FakeLLMClient())
    assert r["refused"] is False
    assert r["citations"], "有材料时必须带引用"
    assert r["citation"]["fabricated"] == 0
    assert r["citation"]["exact"] >= 1


def test_plain_模式不检索也不拒答(db_session, official):
    """无据版对照的定义：**不查也答**。若它在无证据时也拒答，
    它与 grounded 的差异就不再是"有没有检索"，对照实验失效。"""
    r = ask(db_session, 0, "教师法第七条", official, mode="plain", client=FakeLLMClient())
    assert r["tool_calls"] == 0
    assert r["evidence"] == []
    assert r["refused"] is False


def test_引用无法定位时整条回答转拒答():
    """**红线**：只要有一条依据定位不到，就不能原样输出。

    带一条编造依据的回答不能因为"其余都对"而放出去 —— "句句有出处"是产品承诺，
    打折的承诺就不是承诺。
    """
    out = _n_verify(
        {
            "answer": {"citations": [{"quote": "第八十七条 教师有权自行决定教学内容。"}]},
            "observations": [{"items": [{"content": _ART7}]}],
        }
    )
    assert out["refused"] is True
    assert "无法在材料中定位" in out["refusal_reason"]


def test_相关性下限的边界():
    """检索**永远**会返回 top-k，所以「有材料」几乎恒真 —— 必须另设下限。

    ⚠️ 但要说清它挡住了哪一类、没挡住哪一类（实测，见 `eval/teacher_eval.py`）：
    - **挡得住**：与库完全无词形重合的问题（问量子物理而库里只有教育法条）；
    - **挡不住**：「像真的但库里没有」的问题（问《民法典》而库里只有教育法条）——
      这类问法与库共享大量常用字组（「第一」「六十」「十条」「怎么」「规定」），
      词形下限判不出来，而它恰恰是最危险的：模型会自信地引一条不相关的法条作答。

    语义级相关性判定才是正解（复用 `kb_generate._grade_and_filter`），本项未做，
    已记入 ADR-0017 的已知边界 —— **不假装它已经解决**。
    """
    from app.services.teacher_agent import _sufficient

    assert _sufficient([]) is False
    assert _sufficient([{"content": "x", "keyword_score": 0, "rerank_score": -3.0}]) is False
    assert _sufficient([{"content": "x", "keyword_score": 2}]) is True
    assert _sufficient([{"content": "x", "keyword_score": 0, "rerank_score": 5.0}]) is True
    # 结构化定位来的切片没有 keyword_score 键 —— 它是精确证据，不该被下限拦住
    assert _sufficient([{"content": _ART7}]) is True


def test_拒答时不再跑引用校验():
    """否则会打出一条全零的"引用校验"事件，让人以为校验跑过了且没问题。"""
    from app.services.teacher_agent import _route_after_answer

    assert _route_after_answer({"refused": True}) == "end"
    assert _route_after_answer({"refused": False}) == "verify"


def test_工具轮次有硬上限(db_session, official):
    """模型可以一直选工具 —— 没有上限就会撞 LangGraph 的 recursion_limit 报错。

    这里让决策**永远**要查，再断言轮次停在上限处、并且仍然给出了结果
    （有材料就作答，不是超限就拒答）。
    """
    always_search = ['{"tool": "search_kb", "args": {"query": "教师法"}}'] * 10
    client = _StubClient(always_search, json.dumps(
        {"answer": "依据材料", "citations": [{"quote": "教师享有下列权利"}],
         "confidence": "high", "insufficient": False}, ensure_ascii=False))
    r = ask(db_session, 0, "教师法第七条", official, mode="agent", client=client, max_calls=2)
    assert r["tool_calls"] == 2
    assert r["refused"] is False, "超限但有材料时应作答，拒答会让用户白等"


def test_决策不可用时兜底检索而不是失败(db_session, official):
    class Dumb:
        def ask(self, prompt, timeout=30):
            return None  # 模型不可用

    r = ask(db_session, 0, "教师法第七条", official, mode="agent", client=Dumb())
    assert r["evidence"], "决策失败也要把材料找回来，不能让整个问答失败"


def test_回答无法解析时转拒答而不是编一个(db_session, official):
    from app.services.teacher_agent import _n_answer

    class Garbled:
        def ask(self, prompt, timeout=30):
            return "嗯…我觉得这个问题的答案是……"

    out = _n_answer({"client": Garbled(), "question": "q", "observations": [], "mode": "grounded"})
    assert out["refused"] is True and out["answer"] is None


def test_模型自称材料不足时拒答(db_session, official):
    from app.services.teacher_agent import _n_answer

    client = _StubClient([], json.dumps(
        {"answer": "资料里没有相关内容", "citations": [], "confidence": "low", "insufficient": True},
        ensure_ascii=False))
    out = _n_answer({"client": client, "question": "q", "observations": [], "mode": "grounded"})
    assert out["refused"] is True and "insufficient" in out["refusal_reason"]
