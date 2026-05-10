"""`grounded` 的**确定性前置定位** —— 条号类问题误拒的修复与护栏。

## 这条修复来自实测，不是推测

真实模型下的问答基准（`eval/results/teacher_baseline.md`，25 题）里：

| 模式 | 误拒率 | 工具调用均值 |
| --- | --- | --- |
| `grounded`（只有 `search_kb`） | **0.1**（2/20 可答题） | 1.0 |
| `agent`（可调 `lookup_law`） | **0.0** | 1.4 |

而 grounded 那 **2 条误拒样本全是**「《X》第N条 是怎么规定的？」——
两次拒答的理由串都是「材料不足以支撑结论（模型判定 insufficient）」，
说明链路是：条号类查询召回不到那条条文 → 模型看到一堆没回答它的材料 →
**正确地**判 insufficient → 拒答。缺的正是"按法名 + 条号精确取条文"这一步。

## 本文件守住两件事

1. **它真的发生** —— 只写个 `find_law_reference` 没人调用是最容易犯的错（函数单测全绿、链路毫无变化）；
2. **它不会乱发生** —— 没有条号引用的问题不该多查一次（白花成本），
   库里没有该条号时也不该产生任何证据（**不能把模糊命中当答案**，那是"编造条文"的一种形态）。
"""

import pytest

from app.models import Document, DocumentChunk
from app.services.llm_client import FakeLLMClient
from app.services.scope import NAMESPACE_OFFICIAL, Scope
from app.services.teacher_agent import ask
from app.services.tools import find_law_reference

#: ⚠️ 法名必须是**本文件独有**的：本仓测试库是会话级共享的，别的用例会灌入真实法条语料。
#: 若用「教师法」这类真名，`LIKE %教师法%` 会同时命中真实语料，条数断言随机失败。
_TEST_LAW = "前置定位测试法"
_TEST_STORAGE_PATH = "laws/__test_law_ref__.md"

_ROWS = [
    ("前置定位测试法 / 第十五条", "第十五条 本文件专用条文的正文，用来验证结构化定位确实被调用。"),
    ("前置定位测试法 / 第七十七条", "第七十七条 另一条，用来验证条号必须精确相等。"),
]


def _drop(db) -> None:
    """**测试必须清理自己**：会话级共享库，残留会跨文件影响断言"精确 id 集合"的用例。"""
    ids = [r[0] for r in db.query(Document.id).filter(
        Document.storage_path == _TEST_STORAGE_PATH).all()]
    if not ids:
        return
    db.query(DocumentChunk).filter(DocumentChunk.document_id.in_(ids)).delete(
        synchronize_session=False
    )
    db.query(Document).filter(Document.id.in_(ids)).delete(synchronize_session=False)
    db.commit()


def _seed(db) -> None:
    _drop(db)
    doc = Document(
        candidate_id=None,
        is_official=True,
        title=_TEST_LAW,
        file_type="md",
        char_count=sum(len(c) for _h, c in _ROWS),
        chunk_count=len(_ROWS),
        status="parsed",
        storage_path=_TEST_STORAGE_PATH,
    )
    db.add(doc)
    db.flush()
    for seq, (heading, content) in enumerate(_ROWS):
        db.add(DocumentChunk(
            document_id=doc.id, seq=seq, content=content,
            heading_path=heading, char_count=len(content),
        ))
    db.commit()


@pytest.fixture
def official(db_session):
    _seed(db_session)
    yield Scope(namespace=NAMESPACE_OFFICIAL)
    _drop(db_session)


def _tools_used(result: dict) -> list[str]:
    return [ob["tool"] for ob in result["observations"]]


# ---------------- 识别（纯函数，不需要库） ----------------

@pytest.mark.parametrize("question,law,article", [
    ("义务教育法第十五条 是怎么规定的？", "义务教育法", "第十五条"),
    ("《教师法》第七条", "教师法", "第七条"),
    ("根据《中华人民共和国义务教育法》第十五条，教师应当…", "义务教育法", "第十五条"),
    ("中华人民共和国民办教育促进法第十二条怎么说", "民办教育促进法", "第十二条"),
    ("教师资格条例第十九条", "教师资格条例", "第十九条"),
    ("未成年人保护法 第五十条", "未成年人保护法", "第五十条"),
])
def test_识别法名与条号(question, law, article):
    """全称要剥掉「中华人民共和国」前缀 —— 库里语料的法名是简称，
    不剥会让 `LIKE %中华人民共和国义务教育法%` 匹配不到 `义务教育法 / 第十五条`。"""
    ref = find_law_reference(question)
    assert ref == {"law": law, "article": article}


@pytest.mark.parametrize("question", [
    "什么是素质教育？",
    "教育法律法规第七十七条怎么理解",   # 以「规」收尾 → 不是法名，法|条例 这个锚点挡掉
    "教师职业道德规范有哪些内容",
    "教师法是怎么规定的",              # 有法名、没有条号 → 不该触发
    "",
])
def test_不该识别的不识别(question):
    assert find_law_reference(question) is None


# ---------------- 前置定位真的发生（端到端） ----------------

def test_条号类问题在grounded下会走结构化定位(db_session, official):
    """这是本项的核心断言：**链路真的变了**，而不只是多了个没人调的函数。"""
    r = ask(db_session, 0, "前置定位测试法第十五条 是怎么规定的？", official,
            mode="grounded", client=FakeLLMClient())
    assert "lookup_law" in _tools_used(r), f"没走结构化定位：{_tools_used(r)}"
    assert r["tool_calls"] == 2, "应是 检索 + 结构化定位 各一次"
    # 证据里必须有那条精确条文（heading_path 以条号收尾）
    assert any(
        (e.get("heading_path") or "").endswith("第十五条") for e in r["evidence"]
    ), "结构化定位命中了，但条文没进证据"


def test_结构化定位的返回不带检索侧的评分字段(db_session, official):
    """`_sufficient` 的判别式是「**没有** `keyword_score` → 来自结构化定位 → 直接算有据」。

    所以 `lookup_law` 的返回绝不能带检索侧评分字段：一旦带上（比如"统一字段"重构），
    精确命中的条文会掉进**相关性下限**判定 —— 而 `LIKE` 命中的条文未必有关键词分，
    于是**精确证据反被判为不足**，本项修复静默失效（拒绝照旧发生，且看不出原因）。

    这条断言一开始写错了（我去查 evidence 里那片，结果抓到的是 `search_kb` 取到的**同一片**）
    —— 也正是那次写错暴露了 `_evidence_chunks` 不去重的问题。
    """
    from app.services.tools import LOOKUP_LAW, ToolContext, execute

    ctx = ToolContext(db_session, official, 0)
    res = execute(LOOKUP_LAW, {"law": _TEST_LAW, "article": "第十五条"}, ctx)
    assert res["ok"] and res["items"], f"结构化定位没命中：{res}"
    assert all("keyword_score" not in it for it in res["items"])
    assert all((it.get("heading_path") or "").endswith("第十五条") for it in res["items"])


def test_证据按切片去重_同一切片被两条途径取到只算一次():
    """「依据 N 处」是用户直接看到的数字，不能因为"两个途径都取到了"而虚高。

    合成状态而不是走真链路：这样才能**确定性地**造出"同 id 被两条观察各取一次"。
    """
    from app.services.teacher_agent import _evidence_chunks

    state = {"observations": [
        {"tool": "search_kb", "items": [
            {"id": 7, "content": "第十五条 正文", "heading_path": "X / 第十五条",
             "keyword_score": 3},
        ]},
        {"tool": "lookup_law", "items": [
            {"id": 7, "content": "第十五条 正文", "heading_path": "X / 第十五条"},
            {"id": 8, "content": "第十六条 正文", "heading_path": "X / 第十六条"},
        ]},
    ]}
    got = _evidence_chunks(state)
    assert [c["id"] for c in got] == [7, 8], f"去重结果不对：{got}"
    # 保留首次出现 → 留下的是带检索评分的那份（重复本身意味着它被检索到了）
    assert got[0].get("keyword_score") == 3


# ---------------- 护栏：不该乱发生 ----------------

def test_没有条号引用时不多查一次(db_session, official):
    """纯增强的代价必须可控：没有「第N条」就不该多花一次查询。"""
    r = ask(db_session, 0, "什么是素质教育？", official,
            mode="grounded", client=FakeLLMClient())
    assert _tools_used(r) == ["search_kb"], f"多查了：{_tools_used(r)}"
    assert r["tool_calls"] == 1


def test_库里没有该条号时不产生证据也不新增观察(db_session, official):
    """`lookup_law` 精确复核全灭会**如实返回空** —— 那里不能把 `LIKE` 的模糊命中当答案。
    空结果不记观察（否则时间线上会出现一条"成功"却没内容的步骤，误导排查）。"""
    r = ask(db_session, 0, "前置定位测试法第九十九条 是怎么规定的？", official,
            mode="grounded", client=FakeLLMClient())
    assert "lookup_law" not in _tools_used(r), "空结果不该被记成一次成功观察"
    assert not any(
        (e.get("heading_path") or "").endswith("第九十九条") for e in r["evidence"]
    ), "库里没有的条号绝不能出现在证据里"


def test_plain_模式仍然完全不检索(db_session, official):
    """前置定位不能漏进 `plain`：那是"不查也答"的对照档，
    它一旦开始检索，与 grounded 的差异就不再是"有没有检索"，对照实验失效。"""
    r = ask(db_session, 0, "前置定位测试法第十五条 是怎么规定的？", official,
            mode="plain", client=FakeLLMClient())
    assert r["tool_calls"] == 0
    assert r["evidence"] == []
    assert r["observations"] == []
