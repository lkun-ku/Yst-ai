"""官方语料命名空间隔离：越权是最该防的一类错。

**为什么单列一组用例**：官方语料（考纲 / 法条 / rubric）**全员可见**，个人资料**仅本人可见**。
这两类混在一起时**不会报错** —— 检索照常返回，只是把不该看的内容混进了结果，
用户无法察觉。所以这里把「谁能看到什么」写成**会失败的断言**。

覆盖四件事：

1. `Scope` 的三条不变量（构造期拦截非法组合，而不是等到检索时静默出错）；
2. `load_chunks_for_scope` 的命名空间语义，含**越权断言**（A 取不到 B、A 的 personal 取不到官方）；
3. `retrieve()` 走完整检索链路时命名空间依然成立 —— 证明过滤真的发生在召回阶段，
   不是只在某个底层函数里做了；
4. `kb_corpus` 的切分规则与幂等（法条一条一片；灌两次不翻倍）。
"""

import pytest

from app.models import Candidate, Document, DocumentChunk
from app.services.kb_corpus import ingest_official_corpus, split_law_articles
from app.services.kb_retrieval import load_chunks_for_scope, retrieve
from app.services.scope import (
    NAMESPACE_BOTH,
    NAMESPACE_OFFICIAL,
    NAMESPACE_PERSONAL,
    Scope,
)


# ---------------- 夹具 ----------------

def _make_doc(db, *, candidate_id, is_official, title, contents) -> Document:
    """建一份资料 + 切片。`candidate_id=None` + `is_official=True` 即官方语料。"""
    doc = Document(
        candidate_id=candidate_id,
        is_official=is_official,
        title=title,
        file_type="txt",
        char_count=sum(len(c) for c in contents),
        chunk_count=len(contents),
        status="parsed",
        storage_path=title,
    )
    db.add(doc)
    db.flush()
    for seq, content in enumerate(contents):
        db.add(
            DocumentChunk(
                document_id=doc.id,
                seq=seq,
                content=content,
                heading_path=title,
                char_count=len(content),
            )
        )
    db.commit()
    return doc


_TITLES = ("A的私人讲义", "B的私人讲义", "官方·教师法")
_CAND_IDS = (8101, 8102)


def _cleanup(db) -> None:
    """清掉本模块造的数据。

    **为什么需要**：测试库是**会话级**的（`conftest._reset_db` 只在开始跑一次），
    `db_session` 不回滚。所以每个用例都要先把上一轮的同 id 数据清掉，
    否则第 2 个用例就会撞唯一约束 —— 而且报错会指向"插入失败"，而不是真正的问题。
    """
    for title in _TITLES:
        doc = db.query(Document).filter(Document.storage_path == title).one_or_none()
        if doc is not None:
            db.query(DocumentChunk).filter(DocumentChunk.document_id == doc.id).delete(
                synchronize_session=False
            )
            db.delete(doc)
    db.query(Candidate).filter(Candidate.id.in_(_CAND_IDS)).delete(synchronize_session=False)
    db.commit()


@pytest.fixture
def corpus(db_session):
    """两名考生 + 一份官方语料，返回「谁的切片属于谁」的 id 集合，便于断言越权。"""
    db = db_session
    _cleanup(db)
    a = Candidate(id=8101, unionid="ns-a")
    b = Candidate(id=8102, unionid="ns-b")
    db.add_all([a, b])
    db.commit()

    doc_a = _make_doc(
        db, candidate_id=8101, is_official=False, title="A的私人讲义",
        contents=["甲的私人笔记：教师权利的第一条内容", "甲的私人笔记：学生观的核心主张"],
    )
    doc_b = _make_doc(
        db, candidate_id=8102, is_official=False, title="B的私人讲义",
        contents=["乙的私人笔记：完全无关的私人内容"],
    )
    doc_off = _make_doc(
        db, candidate_id=None, is_official=True, title="官方·教师法",
        contents=["第七条 教师享有下列权利", "第八条 教师应当履行下列义务"],
    )

    def ids_of(document_id):
        return {
            c.id for c in db.query(DocumentChunk).filter(DocumentChunk.document_id == document_id)
        }

    return {
        "db": db,
        "a": ids_of(doc_a.id),
        "b": ids_of(doc_b.id),
        "official": ids_of(doc_off.id),
    }


def _seen(chunks) -> set:
    return {c["id"] for c in chunks}


# ---------------- Scope 的三条不变量 ----------------

def test_官方命名空间禁止带考生():
    """官方语料不属于任何人 —— 带上 id 会暗示「某人拥有它」。"""
    with pytest.raises(ValueError, match="官方语料不属于任何考生"):
        Scope(namespace=NAMESPACE_OFFICIAL, candidate_id=8101)


@pytest.mark.parametrize("namespace", [NAMESPACE_PERSONAL, NAMESPACE_BOTH])
def test_个人相关命名空间必须带考生(namespace):
    """留空 candidate_id 等于个人资料对全库可见 —— 这是本项目最不能犯的错。"""
    with pytest.raises(ValueError, match="全库可见"):
        Scope(namespace=namespace)


def test_未知命名空间直接报错():
    """拼错 namespace 若被静默接受，会退化成「不按命名空间过滤」，即全库可见。"""
    with pytest.raises(ValueError, match="namespace 必须是"):
        Scope(namespace="officials", candidate_id=None)


def test_命名空间语义方法():
    official = Scope(namespace=NAMESPACE_OFFICIAL)
    assert official.includes_official() and not official.includes_personal()
    personal = Scope(namespace=NAMESPACE_PERSONAL, candidate_id=8101)
    assert personal.includes_personal() and not personal.includes_official()
    both = Scope(namespace=NAMESPACE_BOTH, candidate_id=8101)
    assert both.includes_official() and both.includes_personal()


# ---------------- 命名空间语义 + 越权 ----------------

def test_个人检索只给本人_且取不到官方语料(corpus):
    """★ 越权断言：甲的个人检索里，既不能有乙的，也不能有官方语料。"""
    seen = _seen(
        load_chunks_for_scope(corpus["db"], Scope(namespace=NAMESPACE_PERSONAL, candidate_id=8101))
    )
    assert seen == corpus["a"], "个人检索必须恰好等于本人的切片"
    assert not (seen & corpus["official"]), "个人检索泄漏了官方语料"
    assert not (seen & corpus["b"]), "个人检索泄漏了他人资料"


def test_官方检索只给官方语料(corpus):
    seen = _seen(load_chunks_for_scope(corpus["db"], Scope(namespace=NAMESPACE_OFFICIAL)))
    assert seen == corpus["official"]
    assert not (seen & corpus["a"]), "官方检索泄漏了个人资料"


def test_两者都查时两边都有(corpus):
    seen = _seen(load_chunks_for_scope(corpus["db"], Scope(namespace=NAMESPACE_BOTH, candidate_id=8101)))
    assert seen == (corpus["a"] | corpus["official"])
    assert not (seen & corpus["b"]), "both 命名空间泄漏了他人资料"


def test_跨考生隔离(corpus):
    """★ 越权断言：乙查不到甲的任何一片。"""
    seen = _seen(
        load_chunks_for_scope(corpus["db"], Scope(namespace=NAMESPACE_PERSONAL, candidate_id=8102))
    )
    assert seen == corpus["b"]
    assert not (seen & corpus["a"])


def test_官方语料的归属是空的(corpus):
    """官方语料 candidate_id 为 None（不是哨兵 id）—— 这样任何按 id 的查询都天然不匹配。"""
    db = corpus["db"]
    official_doc = db.query(Document).filter(Document.is_official.is_(True)).one()
    assert official_doc.candidate_id is None


# ---------------- 检索链路级（证明过滤发生在召回阶段）----------------

def test_检索链路同样遵守命名空间(corpus):
    """底层 `load_chunks_for_scope` 对了还不够 —— 走完整 `retrieve()` 也必须对。

    这条用例防的是「有人新加了一条检索路径，忘记带命名空间」：那种情况下
    底层函数是对的，但链路会静默返回越权内容。
    """
    db = corpus["db"]
    query = "教师享有下列权利"

    personal_seen = _seen(
        retrieve(db, query, Scope(namespace=NAMESPACE_PERSONAL, candidate_id=8101))
    )
    assert not (personal_seen & corpus["official"]), "个人检索链路泄漏了官方语料"

    both_seen = _seen(
        retrieve(db, query, Scope(namespace=NAMESPACE_BOTH, candidate_id=8101))
    )
    assert both_seen, "both 检索应当能召回官方语料"
    assert not (both_seen & corpus["b"]), "both 检索链路泄漏了他人资料"


# ---------------- 官方语料入库：切分与幂等 ----------------

def test_法条按条切分_一条一片():
    """一条一片是 `source_quote` 校验的前提：引用必须完整出现在某一片里。"""
    body = (
        "第一条 甲内容。\n\n"
        "第二条 乙内容，含（一）子项；\n（二）另一个子项。\n\n"
        "第三条 丙内容。"
    )
    plans = split_law_articles("测试法", body)
    assert len(plans) == 3
    assert [p.heading_path for p in plans] == ["测试法 / 第一条", "测试法 / 第二条", "测试法 / 第三条"]
    assert plans[1].content.startswith("第二条")


def test_灌入官方语料是幂等的(db_session):
    """考纲修订后要重跑 —— 重跑必须是"重建"而不是"翻倍"。"""
    first = ingest_official_corpus(db_session, embed=False)
    assert first["docs"] >= 1
    assert first["chunks"] >= 1

    second = ingest_official_corpus(db_session, embed=False)
    assert second["docs"] == first["docs"]
    assert second["chunks"] == first["chunks"], "重复灌入导致切片翻倍"
