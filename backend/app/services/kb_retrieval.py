"""跨文档知识库检索服务（路线② / ③ 共用检索层）。

范围：用户输入自然语言 scope → 在其**个人资料库（多文档）**中召回相关切片 → 供生成层拼上下文。

与单文档 `embedding.retrieve()` 的边界（原「二期跨文档」增量路径，对应 ADR 已删除）：
- 单文档出题继续用 `embedding.retrieve()`（内存、按已加载 chunks）；
- 本模块做**跨文档**：按 `candidate_id` 经 `documents` 关联过滤，从 `document_chunks`
  加载该考生全部切片后做内存混合检索。

检索构成：
- 向量通道：numpy 矩阵化余弦（单用户数千 chunk 毫秒级）；
- 关键词通道：二元组命中（复用 `embedding.keyword_score`）；
- 两路按 **RRF（Reciprocal Rank Fusion）** 融合，叠加标题路径精确命中加成；
- 三级降级：向量或关键词任一可用即融合；两者皆空则均匀采样兜底（保证覆盖）。

返回带分值（fusion_score / vector_score / keyword_score / heading_bonus）的 chunk 列表，
供质量闭环的「相关性评分」节点消费。
**生产 PG 走 pgvector（工单 14）**：`retrieve_by_scope` 按方言自动分发——
PostgreSQL → `retrieve_by_scope_pg`（`embedding_vec VECTOR(1024)` + HNSW + SQL 余弦距离）；
SQLite / dev → 下面的内存混合检索。两条路径返回结构一致，上层无感。

复用 `embedding.py`：embed_one / decode_vector / keyword_score / uniform_sample。
"""

from __future__ import annotations

import numpy as np
import re
from sqlalchemy import select, text as sa_text

from ..models import Document, DocumentChunk
from .embedding import embed_one, decode_vector, keyword_score, uniform_sample

_RRF_K = 60  # Reciprocal Rank Fusion 收敛常数


def _is_pg(db) -> bool:
    """绑定的 DB 引擎是否为 PostgreSQL（pgvector 分支可用）。"""
    try:
        return db.get_bind().dialect.name == "postgresql"
    except Exception:
        return False


# ---------------- 加载 ----------------

def load_chunks(db, candidate_id: int) -> list[dict]:
    """加载该考生个人资料库的全部切片（跨文档），转成检索用 dict 列表。

    含 embedding 的进入向量通道；全部（含无 embedding 者）进入关键词通道，
    保证即便部分切片 embedding 失败也能被关键词/均匀采样兜底召回（三级降级）。
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
    """关键词通道：二元组命中数排序，返回 [(score, chunk_index)] 降序。"""
    scored = [(keyword_score(query, c["content"]), i) for i, c in enumerate(chunks)]
    scored = [(s, i) for s, i in scored if s > 0]
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

def retrieve_by_scope_pg(
    db,
    candidate_id: int,
    scope: str,
    k: int = 8,
    embed_fn=None,
) -> list[dict]:
    """pgvector 路径（工单 14）：单条 SQL 召回 top-k，余弦距离由 HNSW 索引加速。

    与内存 numpy 路径语义对齐：返回结构字段一致（id/document_id/seq/content/heading_path/
    char_count/fusion_score/vector_score/keyword_score/heading_bonus）；仅走向量通道，
    keyword/heading 在 PG 路径下不参与融合（生产 chunk 全部有 embedding，可接受）。
    query embedding 失败 → 空结果（不静默降级；上层可重试或回退到内存路径）。
    """
    fn = embed_fn or embed_one
    try:
        qv = fn(scope) or []
    except Exception:
        qv = []
    if not qv:
        return []

    vec_literal = "[" + ",".join(f"{x:.7f}" for x in qv) + "]"
    sql = sa_text(
        """
        SELECT dc.id, dc.document_id, dc.seq, dc.content, dc.heading_path, dc.char_count,
               1 - (dc.embedding_vec <=> :q::vector) AS sim
        FROM document_chunks dc
        JOIN documents d ON d.id = dc.document_id
        WHERE d.candidate_id = :cid AND dc.embedding_vec IS NOT NULL
        ORDER BY dc.embedding_vec <=> :q::vector
        LIMIT :k
        """
    )
    rows = db.execute(sql, {"q": vec_literal, "cid": candidate_id, "k": k}).fetchall()
    out: list[dict] = []
    for r in rows:
        sim = float(r[6])
        out.append(
            {
                "id": r[0],
                "document_id": r[1],
                "seq": r[2],
                "content": r[3] or "",
                "heading_path": r[4],
                "char_count": r[5] or 0,
                "fusion_score": sim,
                "vector_score": sim,
                "keyword_score": 0,
                "heading_bonus": 0.0,
            }
        )
    return out


def retrieve_by_scope(
    db,
    candidate_id: int,
    scope: str,
    k: int = 8,
    embed_fn=None,
) -> list[dict]:
    """跨文档混合检索主入口。

    按方言自动分发：
    - PostgreSQL → `retrieve_by_scope_pg`（pgvector + HNSW，单条 SQL）
    - SQLite/dev → 内存 numpy 余弦 + 关键词 RRF + 标题加成（原路径）

    返回 top-k chunk，每个含 fusion_score / vector_score / keyword_score / heading_bonus。
    embed_fn 可注入（测试用假向量）；默认 embed_one。
    """
    if _is_pg(db):
        return retrieve_by_scope_pg(db, candidate_id, scope, k, embed_fn)

    chunks = load_chunks(db, candidate_id)
    if not chunks:
        return []

    fn = embed_fn or embed_one
    try:
        qv = fn(scope)
    except Exception:
        qv = None

    vec = vector_rank(qv, chunks)
    kw = keyword_rank(scope, chunks)

    if vec or kw:
        fused = rrf([vec, kw])
        bonus = heading_bonus(scope, chunks)
        ranked = sorted(fused.keys(), key=lambda ci: -(fused[ci] + bonus.get(ci, 0.0)))
        out: list[dict] = []
        for ci in ranked[:k]:
            c = _clean(chunks[ci])
            c["fusion_score"] = round(fused[ci] + bonus.get(ci, 0.0), 4)
            c["vector_score"] = next((s for s, i in vec if i == ci), 0.0)
            c["keyword_score"] = next((s for s, i in kw if i == ci), 0)
            c["heading_bonus"] = bonus.get(ci, 0.0)
            out.append(c)
        return out

    # 三级降级最终兜底：均匀采样（保证覆盖，避免题目扎堆开头）
    return [
        {**_clean(c), "fusion_score": 0.0, "vector_score": 0.0,
         "keyword_score": 0, "heading_bonus": 0.0}
        for c in uniform_sample(chunks, k)
    ]
