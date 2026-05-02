"""稀疏通道：BM25 的机制、**它为什么被否决**、以及 PG 路径的关键词融合。

**为什么单列一组用例**：本项做了一次"升级"（命中数 → BM25），指标把它**否决了**。
这类决定最容易在将来被无声推翻 —— 后来者看到 `BigramBM25` 会以为那是升级方向，
顺手换回生产口径，而回退的代价（recall@1 掉一半）不会以任何形式报错。
所以这里把三件事钉成会失败的断言：

1. BM25 的机制**确实**按它宣称的方式工作（IDF / TF 饱和 / 长度归一）——
   否则"它输了"这个结论本身不成立，可能只是实现错了；
2. 两种口径在构造语料上**取向相反** —— 这是"选哪个"这个问题的可复现最小样本；
3. **生产入口用的是命中数口径** —— 直接锁住 v1 的决定；
4. PG 路径的关键词通道：SQL 构造与融合逻辑（本机无 PG，只能测到这两层，
   **未在真实 PostgreSQL 上验证**，见 `retrieve_by_scope_pg` 的 docstring）。
"""

import pytest

from app.models import Candidate, Document, DocumentChunk
from app.services.embedding import BigramBM25, keyword_score, query_terms
from app.services.kb_retrieval import (
    _fuse_pg_rows,
    _pg_keyword_candidates_sql,
    keyword_rank,
    keyword_rank_bm25,
    retrieve,
)
from app.services.scope import NAMESPACE_PERSONAL, Scope

# ---------------- BM25 机制 ----------------

def test_词项越罕见_idf_越高():
    """注意单位是**二元组**，不是字符：`"甲"` 这个二元组只在"甲"那一片里出现。

    首版断言写成 `idf("甲乙") > idf("甲")` 就踩了这个坑 ——
    我按字符频率以为「甲」高频，实际它的 df 只有 1（更罕见）。
    """
    idx = BigramBM25.fit(["甲乙", "甲乙", "乙丙"])
    assert idx.df["甲乙"] == 2 and idx.df["乙丙"] == 1
    assert idx.idf("乙丙") > idx.idf("甲乙")


def test_语料里没有的词项得零分_而不是负分():
    """IDF 用 `ln(1 + …)` 形式正是为了非负：负 idf 会让"命中"变成扣分。"""
    idx = BigramBM25.fit(["甲乙丙"])
    assert idx.idf("丙丁") == 0.0
    assert all(v >= 0.0 for v in idx.score("丙丁"))


def test_词频饱和_命中两次不到两倍分():
    """必须关掉长度归一来隔离 TF 饱和 —— 这两个效应**方向相反且同时存在**：
    `"甲乙甲乙"` 词频更高（加分），但片也更长（扣分），不隔离就测不出饱和。
    首版没关 b，结果是长的那片反而分更低。
    """
    idx = BigramBM25.fit(["甲乙", "甲乙甲乙"], b=0.0)
    s = idx.score("甲乙")
    assert s[1] > s[0]  # 出现两次确实更高
    assert s[1] < 2 * s[0]  # 但不是线性 —— 这就是 TF 饱和


def test_长度归一_同样命中一次时长文档分更低():
    idx = BigramBM25.fit(["甲乙", "甲乙" + "丙" * 40])
    s = idx.score("甲乙")
    assert s[0] > s[1]


def test_b_为0时不做长度归一():
    idx = BigramBM25.fit(["甲乙", "甲乙" + "丙" * 40], b=0.0)
    assert idx.score("甲乙")[0] == pytest.approx(idx.score("甲乙")[1])


def test_df_按文档计_同一片内出现多次只算一次():
    idx = BigramBM25.fit(["甲乙甲乙甲乙"])
    assert idx.df["甲乙"] == 1
    assert idx.tf[0]["甲乙"] == 3


def test_query_terms_去重保序():
    """重复词项不该重复计分：查询里写两遍同一个词，不是文档更相关的证据。

    用「甲甲甲甲」而不是「教育教育」——后者的二元组是 教育/育教/教育，
    去重后是两项而不是一项，会让这条断言看起来在测别的东西。
    """
    assert query_terms("甲甲甲甲") == ["甲甲"]
    assert query_terms("甲乙丙") == ["甲乙", "乙丙"]


# ---------------- 两种口径的取向差异（"选哪个"的最小可复现样本）----------------

#: 20 片填充文档：都含「教育」「工作」，但**不含**「育工」这个罕见字组。
#: 刻意避开「育工」——填充文本若写成"教育工作"就会产生该字组，df 就不再是 1。
_FILLER = "教育活动与工作安排"
_CRAFTED = [_FILLER] * 20 + ["育工"]
_QUERY = "教育育工工作"  # 词项：教育 / 育育 / 育工 / 工工 / 工作


def test_两种口径在构造语料上取向相反():
    """这是本项决策的机制证明，也是回退的依据。

    - 命中数：填充片命中 2 个词项（教育、工作），目标片只命中 1 个（育工）→ 填充片胜
    - BM25：育工 df=1 拿到极高 IDF，教育/工作 几乎不值钱 → 目标片胜

    **在真实语料上 BM25 输**（见 `BigramBM25` docstring 的实测表），
    原因不是它没按设计工作，而是本项目语料里"罕见词"这一假设不成立：
    法名的字组不在条文正文里，条号的字组每部法都有。
    """
    chunks = [{"content": c} for c in _CRAFTED]
    hits_top = keyword_rank(_QUERY, chunks)[0][1]
    bm25_top = keyword_rank_bm25(_QUERY, chunks)[0][1]
    assert hits_top == 0, "命中数口径应把填充片排第一（它命中了 2 个词项）"
    assert bm25_top == 20, "BM25 应把含罕见字组的目标片排第一（IDF 极高）"


def test_命中数口径忽略词频与文档长度():
    """它只看"命中了几个不同词项" —— 这既是它的弱点，也是它在本语料上赢的原因。"""
    assert keyword_score("教育", "教育教育教育") == keyword_score("教育", "教育")


# ---------------- 生产入口的口径（锁住回退决定）----------------

def _mk_doc(db, cid: int, title: str, contents: list[str]) -> None:
    doc = Document(
        candidate_id=cid,
        is_official=False,
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
                heading_path="无标题",  # 与查询无重叠，避免标题加成干扰本用例
                char_count=len(content),
            )
        )
    db.commit()


def test_生产入口用命中数口径而不是BM25(db_session):
    """直接锁住回退决定：把 `retrieve()` 换成 BM25 会让这条断言失败。

    本机无 PG，故 embed_fn 传 None 让向量通道缺席，链路上只剩稀疏通道 ——
    这样断言的就是"生产排序"本身，而不是某个底层函数。
    """
    cid = 7701
    db_session.add(Candidate(id=cid, unionid="sparse7701"))
    _mk_doc(db_session, cid, "构造语料", _CRAFTED)

    got = retrieve(
        db_session,
        _QUERY,
        Scope(namespace=NAMESPACE_PERSONAL, candidate_id=cid),
        k=3,
        embed_fn=lambda _q: None,
    )
    assert got, "稀疏通道应至少召回一片"
    assert got[0]["content"] == _FILLER, "生产口径是命中数 —— 它把填充片排第一"


# ---------------- PG 路径（本机无 PG，只测 SQL 构造与融合）----------------

def test_PG候选SQL_每个词项一个绑定参数_不拼接用户输入():
    sql, params = _pg_keyword_candidates_sql("d.candidate_id = :cid", ["教育", "工作"], 200)
    assert sql.count("LIKE") == 2
    assert ":t0" in sql and ":t1" in sql
    assert "教育" not in sql, "用户输入绝不能出现在 SQL 文本里"
    assert params == {"t0": "%教育%", "t1": "%工作%", "pool": 200}
    assert "d.candidate_id = :cid" in sql, "命名空间条件必须在 SQL 里（召回阶段过滤）"


def _row(cid, content, sim=None):
    r = [cid, 1, 0, content, "无标题", len(content)]
    if sim is not None:
        r.append(sim)
    return r


def test_PG融合_按id去重且字段齐全():
    vec_rows = [_row(1, "甲乙", 0.9), _row(2, "丙丁", 0.5)]
    kw_rows = [_row(2, "丙丁"), _row(3, "甲乙丙")]
    out = _fuse_pg_rows(vec_rows, kw_rows, "甲乙", k=5)
    ids = [c["id"] for c in out]
    assert len(ids) == len(set(ids)) == 3
    for c in out:
        assert set(c) == {
            "id", "document_id", "seq", "content", "heading_path",
            "char_count", "fusion_score", "vector_score", "keyword_score", "heading_bonus",
        }


def test_PG融合_只被关键词召回的片向量分补零而不是缺席():
    """缺席会让 RRF 只按关键词排名给分，与内存路径行为不一致。"""
    out = _fuse_pg_rows([], [_row(9, "甲乙")], "甲乙", k=5)
    assert out and out[0]["id"] == 9
    assert out[0]["vector_score"] == 0.0


def test_PG融合_两路皆空返回空():
    assert _fuse_pg_rows([], [], "任意", k=5) == []


def test_PG融合_尊重k与等深裁剪():
    vec_rows = [_row(i, "甲乙%d" % i, 1.0 - i * 0.01) for i in range(1, 11)]
    kw_rows = [_row(i, "甲乙%d" % i) for i in range(11, 21)]
    assert len(_fuse_pg_rows(vec_rows, kw_rows, "甲乙", k=4)) == 4
    assert len(_fuse_pg_rows(vec_rows, kw_rows, "甲乙", k=99, pool=3)) <= 13
