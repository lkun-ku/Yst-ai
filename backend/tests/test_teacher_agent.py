"""问答老师：工具层契约、三档模式、以及两条"必须拦住"的红线。

**为什么单列一组用例**：这是本项目第一条**多步自主决策**的链路
（模型自己决定查什么、查几次），也是第一条要同时满足"能答"和"敢拒答"的链路。
它有四种失败模式都不报错，只会安静地给出坏结果：

1. 工具失败被当成异常抛出 → 整个问答崩（应当降级为"用已有材料作答"）；
2. 条号模糊匹配 → 拿「第七十七条」当「第七条」用（**编造条文的一种形态**）；
3. 引用无法定位却照常输出 → "句句有出处"变成口号；
4. 没有证据的回答**冒充有据** → 出口已从"拒答"改为"**无据标注**"
   （2026-06-15 口径变更：允许答，但必须带标记、清空引用、压低置信 ——
   细目见 `test_teacher_fallback.py`）。

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


def _drop_test_official(db) -> None:
    """删掉本文件播种的官方语料（setup 前与 teardown 后都调用）。

    **测试必须清理自己**：本仓测试库是**会话级**重建、不逐用例回滚，
    留下的官方文档能跨文件生效 —— 而 `test_namespace_scope.py` 断言的是
    官方语料的**精确 id 集合**，多一份就失败。
    """
    old = [r[0] for r in db.query(Document.id).filter(
        Document.storage_path == _TEST_STORAGE_PATH).all()]
    if not old:
        return
    db.query(DocumentChunk).filter(DocumentChunk.document_id.in_(old)).delete(
        synchronize_session=False
    )
    db.query(Document).filter(Document.id.in_(old)).delete(synchronize_session=False)
    db.commit()


def _seed_official(db, rows: list[tuple[str, str]]) -> None:
    """建一份官方语料：rows = [(heading_path, content)]。

    ⚠️ **必须先清掉本文件上次留下的那份**：否则同一文件里每个用例都会往同一份库里追加，
    条号查询越查越多条（这个坑让 `test_按条号精确取条文` 一开始返回了 3 条而不是 1 条）。
    """
    _drop_test_official(db)

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
    yield Scope(namespace=NAMESPACE_OFFICIAL)
    # teardown：同 test_mcp_server 的道理 —— 本仓测试库会话级重建、不逐用例回滚，
    # 留下的官方文档会让断言"精确 id 集合"的用例失败（只是靠字母序侥幸没暴露）。
    _drop_test_official(db_session)


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


def test_无据可依时降级为无据回答(db_session, official):
    """⚠️ **口径变更（2026-06-15，实测反馈）**：这里原本断言"没有任何证据 → 拒答"。

    改成「**给答案，但明说它没有出处**」的理由：冷拒答让用户什么也拿不到，
    而模型完全能给出有价值的通识回答（用户原话："没有的内容接 LLM 回答"）。

    本用例仍然钉住**分寸**（没有放松）：无据回答必须**带标记、引用为空、置信 low**
    —— 绝不允许它冒充一条有出处的答案。更细的分寸见 `test_teacher_fallback.py`。
    """
    # 让检索什么都查不到：把 scope 指向一个空的个人库
    empty = Scope(namespace=NAMESPACE_PERSONAL, candidate_id=987654)
    r = ask(db_session, 987654, "随便问问", empty, mode="grounded", client=FakeLLMClient())
    assert r["refused"] is False, "无据时改为降级作答，不再冷拒答"
    assert r["ungrounded"] is True and r["notice"]
    assert r["citations"] == [] and r["confidence"] == "low"


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

    calls: list[str] = []

    class Garbled:
        def ask(self, prompt, timeout=30):
            calls.append(prompt)
            return "嗯…我觉得这个问题的答案是……"

    out = _n_answer({"client": Garbled(), "question": "q", "observations": [], "mode": "grounded"})
    assert out["refused"] is True and out["answer"] is None
    # 两次都不行才拒答（第一次 + 一次格式纠正重试），且拒答原因要写明重试过 ——
    # 否则"重试过但仍失败"与"根本没重试"在用户那里长得一样。
    assert len(calls) == 2
    assert "重试一次" in out["refusal_reason"]


def test_解析失败会重试一次并成功(db_session, official):
    """**真机实测（2026-06-16）**：问「《教师法》第七条规定教师享有哪些权利？」时检索完全正常
    （6 片 = 3 答案库 + 3 官方、第七条排第一），但模型这一次吐出的不是 JSON
    → `parse_teacher_answer` 返回 None → **整条回答变成拒答**；紧接着用同一句话连问两次都正常
    （引用 6 条 / 7 条、置信 high）。

    也就是说这是**偶发的格式失败**，不是能力不足。而代价不对称：重试一次多花一次调用，
    拒答让用户白问一遍 —— 在"句句有出处"的定位下，拒答会被理解成"这题它不会"。

    ⚠️ 重试**不放松契约**：仍然必须是带 citations 的 JSON。所以它与 `_ungrounded_answer`
    那种"降级为无据回答"是**两件事**（后者主动放弃出处）。
    """
    from app.services.teacher_agent import _RETRY_REMINDER, _n_answer

    prompts: list[str] = []

    class Flaky:
        def ask(self, prompt, timeout=30):
            prompts.append(prompt)
            if len(prompts) == 1:
                return "第七条讲了六项权利，我一条条说：\n1. 教育教学…"  # 散文，解析不了
            return json.dumps(
                {"answer": "第七条含六项权利",
                 "citations": [{"quote": "教师享有下列权利"}],
                 "confidence": "high", "insufficient": False},
                ensure_ascii=False,
            )

    out = _n_answer({"client": Flaky(), "question": "q", "observations": [], "mode": "grounded"})

    assert out["refused"] is False
    assert out["answer"]["answer"] == "第七条含六项权利"
    assert out["answer"]["citations"], "重试后仍必须带引用（不放松契约）"
    assert len(prompts) == 2, "应当恰好重试一次"
    assert _RETRY_REMINDER in prompts[1], "重试必须带上格式纠正要求"


def test_模型自称材料不足时降级为无据回答(db_session, official):
    """⚠️ **口径变更（2026-06-15）**：原本断言"模型自称材料不足 → 拒答"。

    实测里最常见的触发场景是**枚举型问题**：问「学校保护有哪些条文」而材料只有三条，
    模型判"不够全"→ 整条拒答。现在改为降级为无据回答。
    """
    from app.services.teacher_agent import _n_answer

    client = _StubClient([], json.dumps(
        {"answer": "资料里没有相关内容", "citations": [], "confidence": "low", "insufficient": True},
        ensure_ascii=False))
    out = _n_answer({"client": client, "question": "q", "observations": [], "mode": "grounded"})
    assert out["refused"] is False and out["ungrounded"] is True
    assert out["answer"]["citations"] == []


# ---------------- 证据层：答案库条目跨观察累加会被稀释官方材料 ----------------


def _obs(items: list[dict]) -> dict:
    return {"tool": "search_kb", "ok": True, "text": "", "error": "", "items": items}


def _item(i: int, source: str) -> dict:
    return {"id": f"it{i}", "content": f"正文{i}", "source_type": source}


def _hop(n: int) -> list[dict]:
    """一次 `search_kb(k=6)` 的典型结果：3 答案库 + 3 官方。"""
    return ([_item(n * 10 + j, "answer_bank") for j in range(3)]
            + [_item(n * 10 + j + 3, "official") for j in range(3)])


def test_答案库证据跨观察有上限而官方不限():
    """`retrieve_for_question` 的配额只在**单次调用内**生效，而证据是跨观察累加的 ——

    原先这一层只有按 id 去重，于是 agent 多跳时答案库条目 3×N 增长，
    官方材料的相对占比被一路稀释。而答案库条目的正文是「题干+选项+答案」，
    **不构成可引用论述**，它挤占的正是官方法条/考纲在上下文里的位置
    （2026-06-15 那次"答案库独占 → 模型只能拒答"就是这个机制）。

    所以答案库**只留首次那一份**，官方不限 —— 多跳的价值在把官方材料找得更全。
    """
    from app.services.teacher_agent import _evidence_chunks

    state = {"observations": [_obs(_hop(i)) for i in range(3)]}
    out = _evidence_chunks(state)

    bank = [x for x in out if x.get("source_type") == "answer_bank"]
    official = [x for x in out if x.get("source_type") == "official"]
    assert len(bank) == 3, f"答案库不该随调用次数累加，实际 {len(bank)}"
    assert len(official) == 9, "官方材料必须保留全部（多跳就是为了找全它）"


def test_单跳时依据构成不变():
    """grounded 只发一次 `search_kb(k=6)` → 3 答案库 + 3 官方。

    这条守的是"上限不能误伤单跳"：`BANK_EVIDENCE_MAX` 取 3 正是为了与
    单次调用的配额对齐，所以 grounded 的行为应当**一字不变**。
    """
    from app.services.teacher_agent import _evidence_chunks

    out = _evidence_chunks({"observations": [_obs(_hop(0))]})
    assert len(out) == 6
    assert len([x for x in out if x.get("source_type") == "answer_bank"]) == 3
