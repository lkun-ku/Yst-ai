"""Embedding 与文档级语义检索。

**为什么不引独立向量数据库（Chroma 等）**：本模块面向**单文档**检索（上限约 200 个 chunk），
200×1024 维暴力余弦在毫秒级完成；Chroma 是为全库百万 chunk 量级设计的独立服务，
在此规模下属过度工程（独立部署 + 双写同步 + 权限重建成本高于收益）。

**跨文档场景已升级（见 ADR-0010，2026-03-29）**：跨文档检索改由 `kb_retrieval.retrieve_by_scope`
承载。生产 PostgreSQL 上启用 **pgvector 扩展**，切片向量字段 `document_chunks.embedding`
在该方言下即 `VECTOR(1024)`（工单 20/W-5 合并双列后只有这一个字段），并建 HNSW 索引；
`retrieve_by_scope` 按方言分发——PG 走 SQL 余弦，SQLite/dev 走本模块的内存检索。
本模块自身（单文档检索）保持不变，不依赖 pgvector。

**三级降级**（任一层失效都不得阻塞出题）：
1. 向量检索（`embed_status == "ok"`）
2. 关键词 / 章节检索
3. 均匀采样 —— 保证覆盖全文档，避免题目扎堆在开头几章
"""

from __future__ import annotations

import math
import re
import time

from ..config import settings

FAKE_DIM = 64
_PUNCT_RE = re.compile(r"[\s，。！？；：、,.!?;:（）()【】\[\]“”\"']")


# ---------------- 向量编解码 ----------------

def encode_vector(vec: list[float] | None) -> bytes | None:
    """float32 序列化入库。"""
    if not vec:
        return None
    import numpy as np

    return np.asarray(vec, dtype="float32").tobytes()


def decode_vector(blob: bytes | None) -> list[float]:
    if not blob:
        return []
    import numpy as np

    return np.frombuffer(blob, dtype="float32").astype(float).tolist()


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


# ---------------- Embedding ----------------

def _fake_embed(text: str, dim: int = FAKE_DIM) -> list[float]:
    """确定性伪向量：不联网、不耗额度，保证测试可离线运行。"""
    vec = [0.0] * dim
    s = (text or "").strip()
    if not s:
        return vec
    for i, ch in enumerate(s):
        vec[(ord(ch) * 31 + i) % dim] += 1.0
    norm = math.sqrt(sum(v * v for v in vec))
    if norm == 0:
        return vec
    return [v / norm for v in vec]


def _real_embed(text: str) -> list[float] | None:
    """OpenAI 兼容 /embeddings 接口。"""
    import json
    import urllib.request

    body = json.dumps({"model": settings.embedding_model, "input": text}).encode("utf-8")
    req = urllib.request.Request(
        settings.embedding_api_base.rstrip("/") + "/embeddings",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {settings.embedding_api_key}",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data["data"][0]["embedding"]


def embed_one(text: str) -> list[float]:
    """单条文本向量化。real 模式失败时静默降级 fake —— 检索失败不得阻塞上传/出题。"""
    if settings.embedding_mode == "real" and settings.embedding_api_base and settings.embedding_api_key:
        try:
            v = _real_embed(text)
            if v:
                return v
        except Exception:
            pass
    return _fake_embed(text)


def wait_embed_ready(
    db,
    *,
    document_id: int | None = None,
    candidate_id: int | None = None,
    timeout: float = 20.0,
    interval: float = 0.5,
) -> bool:
    """等待切片向量化完成——供出题任务在检索之前调用（#23）。

    上传接口为保响应速度把 embedding 放到后台线程；出题任务若在其完成前检索，
    `vector_rank` 的 usable 为空 → `vector_score` 恒为 0 → 静默退化为纯关键词检索，
    出题质量下降且调用方完全无感知。实测：上传后不等待即检索 vector_score=0，
    等待后为 0.82。

    - 判据：范围内切片的 `embed_status` 不再为 `"pending"`（落定为 ok / failed）
    - 返回 True=全部就绪；False=超时——**调用方按三级降级继续，绝不阻塞出题**
    """
    from ..models import Document, DocumentChunk  # 延迟导入：避免与 models 的模块级循环依赖

    def _pending_count() -> int:
        q = db.query(DocumentChunk.id)
        if document_id is not None:
            q = q.filter(DocumentChunk.document_id == document_id)
        elif candidate_id is not None:
            q = q.join(Document, Document.id == DocumentChunk.document_id).filter(
                Document.candidate_id == candidate_id
            )
        return q.filter(DocumentChunk.embed_status == "pending").count()

    deadline = time.time() + timeout
    while True:
        # rollback 结束当前事务快照，否则读不到 embed 线程已提交的新状态（会一直等到超时）
        db.rollback()
        if _pending_count() == 0:
            return True
        if time.time() >= deadline:
            return False
        time.sleep(interval)


# ---------------- 关键词通道 ----------------

def _terms(query: str) -> list[str]:
    q = _PUNCT_RE.sub("", query or "")
    if not q:
        return []
    if len(q) < 2:
        return [q]
    # 中文用二元组，比单字更精确
    return [q[i : i + 2] for i in range(len(q) - 1)]


def keyword_score(query: str, content: str) -> int:
    terms = _terms(query)
    if not terms:
        return 0
    c = content or ""
    return sum(1 for t in terms if t in c)


# ---------------- 均匀采样（兜底通道） ----------------

def uniform_sample(pool: list, k: int) -> list:
    """保序均匀抽样：保证覆盖首尾，避免只命中文档开头。"""
    if not pool:
        return []
    if k >= len(pool):
        return list(pool)
    step = len(pool) / k
    return [pool[int(i * step)] for i in range(k)]


# ---------------- 检索主入口 ----------------

def _filter_scope(chunks: list[dict], scope: list[str] | None) -> list[dict]:
    if not scope:
        return []
    wanted = set(scope)
    return [c for c in chunks if c.get("heading_path") in wanted]


def retrieve(
    query: str,
    chunks: list[dict],
    k: int = 8,
    scope: list[str] | None = None,
    embed_fn=None,
) -> list[dict]:
    """混合检索，带三级降级。

    chunks 元素需含 `content`，可选 `heading_path` / `embedding` / `embed_status`。
    """
    if not chunks:
        return []
    pool = _filter_scope(chunks, scope) or chunks

    # 通道 1：向量检索
    fn = embed_fn or embed_one
    try:
        qv = fn(query)
    except Exception:
        qv = None
    if qv:
        pairs = []
        for c in pool:
            if c.get("embed_status") == "ok" and c.get("embedding"):
                try:
                    pairs.append((cosine_similarity(qv, decode_vector(c["embedding"])), c))
                except Exception:
                    continue
        if pairs:
            pairs.sort(key=lambda x: -x[0])
            return [c for _, c in pairs[:k]]

    # 通道 2：关键词检索
    scored = [(keyword_score(query, c.get("content") or ""), c) for c in pool]
    scored = [p for p in scored if p[0] > 0]
    if scored:
        scored.sort(key=lambda x: -x[0])
        return [c for _, c in scored[:k]]

    # 通道 3：均匀采样兜底
    return uniform_sample(pool, k)
