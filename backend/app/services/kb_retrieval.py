"""跨文档知识库检索服务（路线② / ③ 共用检索层）。

范围：用户输入自然语言 scope → 在其**个人资料库（多文档）**中召回相关切片 → 供生成层拼上下文。

与单文档 `embedding.retrieve()` 的边界（原「二期跨文档」增量路径，对应 ADR 已删除）：
- 单文档出题继续用 `embedding.retrieve()`（内存、按已加载 chunks）；
- 本模块做**跨文档**：按 `candidate_id` 经 `documents` 关联过滤，从 `document_chunks`
  加载该考生全部切片后做内存混合检索。

检索构成：
- 向量通道：numpy 矩阵化余弦（单用户数千 chunk 毫秒级）；
- 关键词通道：二元组命中数（`embedding.keyword_score`）；
- 两路按 **RRF（Reciprocal Rank Fusion）** 融合，叠加标题路径精确命中加成；
- 三级降级：向量或关键词任一可用即融合；两者皆空则均匀采样兜底（保证覆盖）。

**稀疏通道试过升级为 BM25，被指标否决并回退**（2026-06-11）：
在 `eval/datasets/官方法条/` 上实测，BM25 的 recall@1 仅为命中数口径的**一半以下**
（0.26~0.38 vs 0.73），扫遍 k1/b 仍差一倍以上。
根因不是实现问题，是**本场景的信号结构**：法名的 bigram（`育法`/`法第`）不在条文正文里，
条号的 bigram（`第一`/`一条`）每部法都有 —— 于是 IDF 压掉的恰好是唯一还能用的那批中等频率内容词。
完整数据与反转条件见 `embedding.BigramBM25` 的 docstring 与 ADR-0015。
**这张"升级失败"的表保留在 `eval/retrieval_eval.py`** ——
它既是回退决策的依据，也是将来语料变大后重新评估的基线。

返回带分值（fusion_score / vector_score / keyword_score / heading_bonus）的 chunk 列表，
供质量闭环的「相关性评分」节点消费。
**生产 PG 走 pgvector（工单 14）**：`retrieve_by_scope` 按方言自动分发——
PostgreSQL → `retrieve_by_scope_pg`（向量走 HNSW + SQL 余弦；关键词走 SQL 粗筛 + 应用层**同口径**打分，
两路 RRF 融合）；SQLite / dev → 下面的内存混合检索。两条路径返回结构**字段一致**，
且**用同一个稀疏打分函数**（换方言不该改变检索效果）。

复用 `embedding.py`：embed_one / decode_vector / keyword_score / uniform_sample。
"""

from __future__ import annotations

import logging

import numpy as np
import re
from typing import Sequence
from sqlalchemy import and_, or_, select, text as sa_text

from ..config import settings
from ..models import Document, DocumentChunk
from .embedding import (
    BigramBM25,
    decode_vector,
    embed_one,
    keyword_score,
    query_terms,
    uniform_sample,
)
from .rerank import get_reranker
from .scope import NAMESPACE_BOTH, NAMESPACE_OFFICIAL, NAMESPACE_PERSONAL, Scope

logger = logging.getLogger(__name__)

_RRF_K = 60  # Reciprocal Rank Fusion 收敛常数


def _is_pg(db) -> bool:
    """绑定的 DB 引擎是否为 PostgreSQL（pgvector 分支可用）。"""
    try:
        return db.get_bind().dialect.name == "postgresql"
    except Exception:
        return False


# ---------------- 加载 ----------------

def load_chunks(db, candidate_id: int) -> list[dict]:
    """加载某考生**个人**资料库的全部切片（跨文档），转成检索用 dict 列表。

    含 embedding 的进入向量通道；全部（含无 embedding 者）进入关键词通道，
    保证即便部分切片 embedding 失败也能被关键词/均匀采样兜底召回（三级降级）。

    ⚠️ **本函数只看个人资料**（不含官方语料）。需要官方语料请用
    `load_chunks_for_scope` —— 命名空间是显式传入的，不要靠默认参数"顺带"带上，
    否则新增调用点时很容易忘记自己在查哪个范围。
    """
    rows = (
        db.execute(
            select(DocumentChunk)
            .join(Document, Document.id == DocumentChunk.document_id)
            .where(Document.candidate_id == candidate_id)
        )
        .scalars()
        .all()
    )
    out: list[dict] = []
    for c in rows:
        emb = decode_vector(c.embedding) if c.embedding else []
        out.append(
            {
                "id": c.id,
                "document_id": c.document_id,
                "seq": c.seq,
                "content": c.content or "",
                "heading_path": c.heading_path,
                "char_count": c.char_count or len(c.content or ""),
                "embedding": emb,  # [] 表示无可用向量（降级）
                "has_vec": bool(emb),
            }
        )
    return out


def load_chunks_for_scope(db, scope: Scope) -> list[dict]:
    """按 `Scope` 的命名空间加载切片 —— **权限过滤发生在召回阶段**。

    为什么不"先检索后过滤"：那样无权文档会先进入候选集，挤占 top-k、
    污染 RRF 排名，而且**内容已经进了上下文**，过滤就失去意义了。

    命名空间 → 条件（官方语料的 `candidate_id` 为 NULL，天然不匹配任何用户）：
      - `official`  → 仅官方语料
      - `personal`  → 仅本考生资料
      - `both`      → 本考生资料 + 官方语料
    """
    if scope.namespace == NAMESPACE_OFFICIAL:
        where_clause = Document.is_official.is_(True)
    elif scope.namespace == NAMESPACE_BOTH:
        where_clause = or_(
            Document.candidate_id == scope.candidate_id, Document.is_official.is_(True)
        )
    else:
        where_clause = and_(
            Document.candidate_id == scope.candidate_id, Document.is_official.is_(False)
        )

    rows = (
        db.execute(
            select(DocumentChunk).join(Document, Document.id == DocumentChunk.document_id).where(where_clause)
        )
        .scalars()
        .all()
    )
    return [_chunk_row(c) for c in rows]


def _chunk_row(c) -> dict:
    """一个 ORM 切片 → 检索用 dict（含 embedding 与降级标记）。"""
    emb = decode_vector(c.embedding) if c.embedding else []
    return {
        "id": c.id,
        "document_id": c.document_id,
        "seq": c.seq,
        "content": c.content or "",
        "heading_path": c.heading_path,
        "char_count": c.char_count or len(c.content or ""),
        "embedding": emb,  # [] 表示无可用向量（降级）
        "has_vec": bool(emb),
    }


def _clean(c: dict) -> dict:
    """剔除内部键，返回对外 chunk 字典。"""
    return {
        "id": c["id"],
        "document_id": c["document_id"],
        "seq": c["seq"],
        "content": c["content"],
        "heading_path": c["heading_path"],
        "char_count": c["char_count"],
    }


# ---------------- 通道 ----------------

def vector_rank(query_vec: list[float] | None, chunks: list[dict]) -> list[tuple[float, int]]:
    """向量通道：numpy 矩阵化余弦，返回 [(score, chunk_index)] 降序。

    与 `embedding.cosine_similarity` 语义一致，但以矩阵运算避免逐条 Python 循环。
    """
    usable = [(i, c["embedding"]) for i, c in enumerate(chunks) if c["has_vec"]]
    if not query_vec or not usable:
        return []
    idx = np.array([i for i, _ in usable], dtype=np.intp)
    mat = np.asarray([v for _, v in usable], dtype=np.float32)
    q = np.asarray(query_vec, dtype=np.float32)
    qn = q / (np.linalg.norm(q) + 1e-12)
    mn = mat / (np.linalg.norm(mat, axis=1, keepdims=True) + 1e-12)
    sims = mn @ qn  # (n,)
    order = np.argsort(-sims)
    return [(float(sims[j]), int(idx[j])) for j in order]


def keyword_rank(query: str, chunks: list[dict]) -> list[tuple[int, int]]:
    """关键词通道**旧口径**：二元组命中数排序。

    保留只为消融对照（`eval/retrieval_eval.py` 的新旧并排表）——
    没有旧口径的数字，「换成 BM25 更有效」这句话无法证明。生产请用 `keyword_rank_bm25`。
    """
    scored = [(keyword_score(query, c["content"]), i) for i, c in enumerate(chunks)]
    scored = [(s, i) for s, i in scored if s > 0]
    scored.sort(key=lambda x: -x[0])
    return scored


def keyword_rank_bm25(
    query: str, chunks: list[dict], index: BigramBM25 | None = None
) -> list[tuple[float, int]]:
    """关键词通道的 **BM25 变体** —— **仅供消融对照，生产不用**（见 `BigramBM25` docstring）。

    保留它的唯一理由是让 `eval/retrieval_eval.py` 能把新旧口径并排跑一张表：
    「升级为 BM25」这个决定是被那张表**否决**的，表与代码一起留着，
    将来语料规模变化时可以原地重跑、重判。

    `index` 可由调用方预先建好复用（同一批切片多次查询时不必重复 fit）。

    返回的 score 是**绝对值、不可跨查询比较**的 BM25 分（含 IDF，随语料变化）——
    它只用于排序与展示，**不要**拿它做阈值判定（阈值会随语料规模漂移）。
    """
    if not chunks:
        return []
    idx = index or BigramBM25.fit([c.get("content") or "" for c in chunks])
    scored = [(float(s), i) for i, s in enumerate(idx.score(query)) if s > 0]
    scored.sort(key=lambda x: -x[0])
    return scored


# ---------------- 融合 ----------------

def rrf(lists: list[list[tuple[float, int]]]) -> dict[int, float]:
    """Reciprocal Rank Fusion：多路召回融合为 chunk_index -> 融合分。"""
    fused: dict[int, float] = {}
    for lst in lists:
        for rank, (_score, ci) in enumerate(lst):
            fused[ci] = fused.get(ci, 0.0) + 1.0 / (_RRF_K + rank + 1)
    return fused


def heading_bonus(scope: str, chunks: list[dict]) -> dict[int, float]:
    """标题路径精确命中加成：scope 术语命中 chunk 的 heading_path 时加分（上限 0.5）。

    术语切分与 embedding._terms 一致：中文取二元组、拉丁词按空白切分，避免
    对中文按单字切分导致 len>=2 永远为空（此前 bug）。
    """
    if not scope:
        return {}
    terms = _scope_terms(scope)
    if not terms:
        return {}
    bonus: dict[int, float] = {}
    for i, c in enumerate(chunks):
        hp = c["heading_path"] or ""
        if not hp:
            continue
        hit = sum(1 for t in terms if t and t in hp)
        if hit:
            bonus[i] = min(hit * 0.1, 0.5)
    return bonus


_SCOPE_PUNCT_RE = re.compile(r"[\s，。！？；：、,.!?;:（）()【】\[\]“”\"'《》<>]")


def _scope_terms(scope: str) -> list[str]:
    """scope 术语切分：中文→二元组（如 "教育基础"→["教育","育基","基础"]），拉丁→按空白词。"""
    q = _SCOPE_PUNCT_RE.sub("", scope or "")
    if len(q) < 2:
        return [q] if q else []
    if re.search(r"[\u4e00-\u9fff]", q):
        return [q[i : i + 2] for i in range(len(q) - 1)]
    return [t for t in q.split() if t]


# ---------------- 主入口 ----------------

def _pg_namespace_clause(scope_obj: Scope) -> tuple[str, dict]:
    """PG 路径的命名空间条件（SQL 片段来自常量，无字符串拼接注入面）。

    布尔字面量用 `IS TRUE` / `IS FALSE` 而不是 `= 1` —— 后者在 PostgreSQL 上
    是「boolean = integer」，会直接报错（SQLite 才用 1/0）。
    """
    if scope_obj.namespace == NAMESPACE_OFFICIAL:
        return "d.is_official IS TRUE", {}
    if scope_obj.namespace == NAMESPACE_BOTH:
        return "(d.candidate_id = :cid OR d.is_official IS TRUE)", {"cid": scope_obj.candidate_id}
    return "(d.candidate_id = :cid AND d.is_official IS FALSE)", {"cid": scope_obj.candidate_id}


#: PG 双通道的候选深度：**两路取同样的深度再融合**。
#: RRF 只看排名不看分数，两路长度悬殊会让"长的那一路"的末尾条目凭空拿到排名分。
_PG_POOL = 60
#: 关键词 SQL 粗筛的候选上限（比 `_PG_POOL` 宽，留出被 BM25 重排裁剪的余量）。
_PG_KEYWORD_POOL = 200


def _pg_keyword_candidates_sql(ns_sql: str, terms: Sequence[str], pool: int) -> tuple[str, dict]:
    """构造 PG 关键词候选的 SQL 与参数（**纯函数，可单测**）。

    每个词项一个 `LIKE :t{i}` **绑定参数** —— 不拼接任何用户输入；
    `ns_sql` 也只来自 `_pg_namespace_clause` 的常量，没有注入面。

    为什么 SQL 只做"粗筛"而不直接算 BM25：**PG 默认没有中文分词**，
    算不出词频与文档频率；而按 LIKE 命中数排序就退回了被淘汰的"命中数"口径。
    所以这里只把候选缩到可算规模，精算交给应用层的 `BigramBM25`。
    """
    likes = " OR ".join(f"dc.content LIKE :t{i}" for i in range(len(terms)))
    sql = (
        "SELECT dc.id, dc.document_id, dc.seq, dc.content, dc.heading_path, dc.char_count "
        "FROM document_chunks dc "
        "JOIN documents d ON d.id = dc.document_id "
        f"WHERE ({ns_sql}) AND ({likes}) "
        "LIMIT :pool"
    )
    params: dict = {f"t{i}": f"%{t}%" for i, t in enumerate(terms)}
    params["pool"] = pool
    return sql, params


def _fuse_pg_rows(
    vec_rows,
    kw_rows,
    query: str,
    k: int,
    pool: int = _PG_POOL,
) -> list[dict]:
    """把「向量行」与「关键词候选行」融合成最终结果 —— **不碰数据库，因而可单测**。

    PG 路径的融合逻辑全部收在这里，`retrieve_by_scope_pg` 只负责执行两条 SQL。
    这样最容易出错的部分（去重、序号对齐、两路等深裁剪、RRF、标题加成、缺字段补齐）
    可以在 SQLite 上用**合成行**验证，而不需要真的起一个 PostgreSQL。

    行布局（两条 SQL 必须一致）：
        0)id 1)document_id 2)seq 3)content 4)heading_path 5)char_count
        向量行另有第 7 列 = 余弦相似度；关键词候选行没有第 7 列。

    **与内存路径的口径**：稀疏打分用**同一个** `keyword_score`（命中数）——
    两条路径必须同口径，否则"换方言"就等于换了检索效果，而换方言不该改变结果。
    """
    seen: dict = {}
    vec_score: dict = {}
    for r in vec_rows or []:
        seen[r[0]] = r
        vec_score[r[0]] = float(r[6]) if len(r) > 6 and r[6] is not None else 0.0
    for r in kw_rows or []:
        seen.setdefault(r[0], r)
    if not seen:
        return []

    rows = list(seen.values())
    idx_of = {r[0]: i for i, r in enumerate(rows)}

    vec_ranked = [(vec_score[r[0]], idx_of[r[0]]) for r in rows if vec_score.get(r[0], 0.0) > 0.0]
    vec_ranked.sort(key=lambda x: -x[0])

    kw_ranked = [(keyword_score(query, r[3] or ""), i) for i, r in enumerate(rows)]
    kw_ranked = [(s, i) for s, i in kw_ranked if s > 0]
    kw_ranked.sort(key=lambda x: -x[0])
    kw_ranked = kw_ranked[:pool]  # 与向量路等深，避免长列表凭空带来排名分

    fused = rrf([vec_ranked, kw_ranked])
    bonus = heading_bonus(query, [{"heading_path": r[4]} for r in rows])
    order = sorted(fused.keys(), key=lambda i: -(fused[i] + bonus.get(i, 0.0)))

    out: list[dict] = []
    for i in order[:k]:
        r = rows[i]
        out.append(
            {
                "id": r[0],
                "document_id": r[1],
                "seq": r[2],
                "content": r[3] or "",
                "heading_path": r[4],
                "char_count": r[5] or 0,
                "fusion_score": round(fused[i] + bonus.get(i, 0.0), 4),
                "vector_score": vec_score.get(r[0], 0.0),
                "keyword_score": next((s for s, j in kw_ranked if j == i), 0.0),
                "heading_bonus": bonus.get(i, 0.0),
                "rerank_score": None,  # 与内存路径字段一致：精排由调用方在融合后叠加
            }
        )
    return out


def retrieve_by_scope_pg(
    db,
    candidate_id: int,
    scope: str,
    k: int = 8,
    embed_fn=None,
    scope_obj: Scope | None = None,
) -> list[dict]:
    """pgvector 路径（工单 14）：**向量 ⊕ 关键词双通道**，两路 RRF 融合后取 top-k。

    `scope_obj` 为 None 时保持**旧行为**（仅本人资料）；传入 `Scope` 则按命名空间过滤。
    做成可选参数而不是必填，是为了让 `retrieve_by_scope` / `run_eval` 等既有调用点
    **逐步迁移** —— 不在一次改动里动所有调用方，降低回归面。新代码请直接用 `retrieve()`。

    **本次修复的缺口**：此前 PG 路径**只走向量通道**，关键词不参与融合。
    当时的理由是"生产 chunk 全部有 embedding，可接受"—— 这个推理只覆盖了
    「能不能召回」，没覆盖「召回得准不准」：像「《教师法》第七条」「试卷代码 101」
    这类编号与专名在向量空间里接近噪声，纯向量会稳定返回"数学上很像"的错误条款。

    **embedding 失败时不再直接返回空**：改为只走关键词通道 ——
    原实现在 query embedding 失败时静默返回空结果，那是把"部分能力可用"
    误报成"没有数据"。

    ⚠️ **本分支未在真实 PostgreSQL 上验证过**（本机与 CI 都没有 PG 实例）。
    已验证的部分：`_pg_keyword_candidates_sql` 的 SQL/参数构造、
    `_fuse_pg_rows` 的融合逻辑（合成行单测）、SQLite 下的方言分发。
    `<=>` / `CAST(... AS vector)` / `IS TRUE` 这些 PG 专有写法**沿用既有代码**，本次未改语义 ——
    但"未改动"不等于"已验证"，它此前也从未在真 PG 上跑过。
    """
    ns = scope_obj or Scope(namespace=NAMESPACE_PERSONAL, candidate_id=candidate_id)
    ns_sql, ns_params = _pg_namespace_clause(ns)

    fn = embed_fn or embed_one
    try:
        qv = fn(scope) or []
    except Exception:
        qv = []

    vec_rows: list = []
    if qv:
        vec_literal = "[" + ",".join(f"{x:.7f}" for x in qv) + "]"
        # 注意：必须用 CAST(:q AS vector) 而非 :q::vector —— SQLAlchemy 的 text() 会把
        # `::` 视为转义/转换符，导致 :q 不被识别为绑定参数而静默丢失（参数里只剩 cid/k，
        # 运行时报 "could not determine data type of parameter"）。
        vec_sql = sa_text(
            f"""
            SELECT dc.id, dc.document_id, dc.seq, dc.content, dc.heading_path, dc.char_count,
                   1 - (dc.embedding <=> CAST(:q AS vector)) AS sim
            FROM document_chunks dc
            JOIN documents d ON d.id = dc.document_id
            WHERE {ns_sql} AND dc.embedding IS NOT NULL
            ORDER BY dc.embedding <=> CAST(:q AS vector)
            LIMIT :k
            """
        )
        vec_rows = db.execute(
            vec_sql, {"q": vec_literal, "k": _PG_POOL, **ns_params}
        ).fetchall()

    terms = query_terms(scope)
    kw_rows: list = []
    if terms:
        kw_sql, kw_params = _pg_keyword_candidates_sql(ns_sql, terms, _PG_KEYWORD_POOL)
        kw_rows = db.execute(sa_text(kw_sql), {**kw_params, **ns_params}).fetchall()

    return _fuse_pg_rows(vec_rows, kw_rows, scope, k)


def _apply_rerank(reranker, query: str, candidates: list[dict], k: int) -> list[dict]:
    """**第二阶段**：把召回池重排后取前 k（见 `services/rerank.py`）。

    `reranker is None`（`RERANK_IMPL=off`）时只做截断 —— **行为与接入精排前逐字节一致**。
    这一点是刻意保证的：否则"开启精排带来的差异"就混进了别的变化，无法归因。

    精排器 `available()` 为假（权重没下 / 加载失败）时同样退回原顺序，
    并且**只在日志里说**——不抛错、不阻塞检索（精排是增强不是依赖）。
    """
    if reranker is None or not candidates:
        return candidates[:k]
    if not reranker.available():
        logger.warning("精排不可用，按召回顺序返回：%s", reranker.status())
        return candidates[:k]
    return reranker.rerank(query, candidates, k)


def retrieve(
    db,
    query: str,
    scope: Scope,
    k: int = 8,
    embed_fn=None,
) -> list[dict]:
    """跨文档混合检索主入口 —— **新代码请用这个**。

    ⚠️ **术语冲突提醒（务必先读）**：本模块历史里 `scope` 一直是「自然语言查询文本」
    的意思（见下方 `retrieve_by_scope` 的 `scope: str`）；而 `Scope` 类是**过滤条件对象**。
    两者同名不同义。本函数用 `query` 表示查询文本、`scope` 表示 `Scope` 对象来区分 ——
    别再往这个函数里塞第二个叫 scope 的字符串参数。

    按方言自动分发：
    - PostgreSQL → `retrieve_by_scope_pg`（pgvector + HNSW，单条 SQL）
    - SQLite/dev → 内存 numpy 余弦 + 关键词 RRF + 标题加成

    **命名空间过滤在召回阶段完成**（`load_chunks_for_scope`），不是先检索后过滤 ——
    否则无权文档会先进入候选、挤占 top-k、污染 RRF 排名，且内容已进上下文。
    """
    reranker = get_reranker()
    # 开了精排就要**多召回**：精排只能重排已有候选，收益上限由池深决定（上限 = recall@pool）。
    # 关掉精排时 pool == k，行为与接入前逐字节一致。
    pool = max(k, settings.rerank_pool) if reranker is not None else k

    if _is_pg(db):
        candidate = scope.candidate_id
        rows = retrieve_by_scope_pg(db, candidate, query, pool, embed_fn, scope_obj=scope)
        return _apply_rerank(reranker, query, rows, k)

    chunks = load_chunks_for_scope(db, scope)
    if not chunks:
        return []

    fn = embed_fn or embed_one
    try:
        qv = fn(query)
    except Exception:
        qv = None

    vec = vector_rank(qv, chunks)
    # 稀疏通道用命中数口径：BM25 在本项目语料上实测更差，升级被指标否决（见模块 docstring）
    kw = keyword_rank(query, chunks)

    if vec or kw:
        fused = rrf([vec, kw])
        bonus = heading_bonus(query, chunks)
        ranked = sorted(fused.keys(), key=lambda ci: -(fused[ci] + bonus.get(ci, 0.0)))
        out: list[dict] = []
        for ci in ranked[:pool]:
            c = _clean(chunks[ci])
            c["fusion_score"] = round(fused[ci] + bonus.get(ci, 0.0), 4)
            c["vector_score"] = next((s for s, i in vec if i == ci), 0.0)
            c["keyword_score"] = next((s for s, i in kw if i == ci), 0)
            c["heading_bonus"] = bonus.get(ci, 0.0)
            c["rerank_score"] = None
            out.append(c)
    else:
        # 三级降级最终兜底：均匀采样（保证覆盖，避免题目扎堆开头）
        out = [
            {**_clean(c), "fusion_score": 0.0, "vector_score": 0.0,
             "keyword_score": 0, "heading_bonus": 0.0, "rerank_score": None}
            for c in uniform_sample(chunks, pool)
        ]

    return _apply_rerank(reranker, query, out, k)


def retrieve_by_scope(
    db,
    candidate_id: int,
    scope: str,
    k: int = 8,
    embed_fn=None,
) -> list[dict]:
    """旧入口：查某考生的**个人**资料库。这里 `scope` 是自然语言查询文本（不是 `Scope`）。

    保留它是为了不让既有调用点（`kb_generate` / `kb_graph` / `run_eval` / `retrieval_eval`）
    在一次改动里全动 —— 一次改太多调用点是回归的主要来源。

    **新代码请用 `retrieve()`**：它显式传 `Scope`，才能表达「查官方 / 查个人 / 两者都查」。
    """
    return retrieve(
        db,
        scope,
        Scope(namespace=NAMESPACE_PERSONAL, candidate_id=candidate_id),
        k,
        embed_fn,
    )
