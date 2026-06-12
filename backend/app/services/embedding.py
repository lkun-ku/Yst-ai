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
from dataclasses import dataclass
from typing import Sequence

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


def _local_embed(text: str) -> list[float] | None:
    """本地 ONNX 向量模型（`services/local_embed.py`）。

    权重缺失 / 未配 `local` 模式时返回 `None`（让调用方按各自策略降级）——
    这里**不抛异常**：`local` 模式下权重可能还没下（首次部署），
    那是"暂时不可用"而不是"调用出错"，两种情况调用方的处置相同。
    """
    from .local_embed import get_embedder

    embedder = get_embedder()
    if embedder is None or not embedder.available():
        return None
    return embedder.embed_one(text)


def embed_one(text: str) -> list[float]:
    """单条文本向量化。real / local 模式失败时静默降级 fake —— 检索失败不得阻塞上传/出题。"""
    if settings.embedding_mode == "local":
        try:
            v = _local_embed(text)
            if v:
                return v
        except Exception:
            pass
    elif settings.embedding_mode == "real" and settings.embedding_api_base and settings.embedding_api_key:
        try:
            v = _real_embed(text)
            if v:
                return v
        except Exception:
            pass
    return _fake_embed(text)


def embed_query(text: str) -> list[float]:
    """**查询侧**向量化 —— 与文档侧 `embed_one` 分开。

    ## 为什么查询与文档必须是两个函数

    检索的对称性是**可以被故意打破**的：BGE 系列官方要求在**查询**前加一句指令
    （`为这个句子生成表示以用于检索相关文章：`），文档侧**不加**。这是模型训练方式决定的
    （查询是短问句、文档是长段落，非对称训练），加了能明显提升短查询的召回。

    而这件事**不能写进 `embed_one`**：`embed_one` 同时被文档入库路径
    （`routers/documents.py` 上传切片）调用 —— 在那里加前缀会让**文档向量也被污染**，
    两边都加了等于两边都没加，而且没有任何报错。

    默认前缀为空（`settings.embedding_query_prefix`）：它只对 BGE 系有效，
    对 `text-embedding-v3` 这类模型反而引入噪声，所以由使用方按模型显式开。

    ## 实测（法条域，n=135，`eval/retrieval_eval.py --domain 官方法条 --official --auto`）

    | 口径 | 无前缀 | 带前缀 | Δ |
    | --- | --- | --- | --- |
    | dense recall@1 | 0.3852 | **0.4074** | +2.2pp |
    | dense recall@5 | 0.6889 | **0.7407** | +5.2pp |
    | dense recall@10 | 0.7852 | **0.8296** | +4.4pp |
    | dense MRR | 0.5246 | **0.5554** | +3.1pp |
    | dense nDCG | 0.5766 | **0.6146** | +3.8pp |
    | 生产 RRF(命中数)+标题 @1 | 0.7111 | **0.7185** | +0.7pp |

    六项上升、一项（生产档 recall@10）从 0.9852 微降到 0.9704。方向一致，故保留。
    ⚠️ **测量时踩到的坑**：`eval/retrieval_eval.py` 原先自己 `import embed_one` 算查询向量，
    绕过了这个函数 —— 于是"加了前缀"与"没加前缀"跑出来**逐位相同**，看起来像"前缀无效"。
    评测必须走**生产在跑的那个函数**，否则测的是评测自己的实现。
    """
    prefix = settings.embedding_query_prefix
    return embed_one(f"{prefix}{text}" if prefix and text else text)


# ---------------- 严格向量化（官方语料灌入专用） ----------------

#: 向量来源取值。`strict_embed` 用它把"刻意离线"与"真的失败了"区分开。
EMBED_REAL = "real"
EMBED_LOCAL = "local"     # 本地 ONNX 模型 —— **同样是真向量**，只是不经过任何供应商
EMBED_FAKE = "fake"       # 刻意用 fake 模式：离线开发 / 测试
EMBED_FAILED = "failed"   # 配的是 real / local 却没拿到 → 必须显式失败，不许伪装成成功

#: 真向量来源（可以落 `embed_status='ok'`）。**`fake` 不在其中** —— 伪向量必须看得见。
REAL_SOURCES = (EMBED_REAL, EMBED_LOCAL)


def declared_dim() -> int | None:
    """`document_chunks.embedding` 列**声明**的维度（仅 PG 的 `VECTOR(N)` 有）。

    SQLite / dev 是 `LargeBinary`（没有维度概念）→ 返回 `None`，那条路径本就不做向量检索。
    对着列声明取值而**不是硬编码 1024**：将来把模型换成 3072 维，这条校验跟着迁移走，不会失守。
    """
    from ..models import DocumentChunk  # 延迟导入：避免与 models 的循环依赖

    col_type = DocumentChunk.__table__.c.embedding.type
    return getattr(col_type, "dim", None)


def _with_dim_check(v: list[float], source: str) -> tuple[list[float] | None, str, str]:
    """维度与列声明对不上就判失败 —— 这是向量入库的最后一道闸。

    只有 PG 的 `VECTOR(N)` 有维度声明（SQLite 是 `LargeBinary`，`declared_dim()` 返回 None，
    本就不做向量检索），所以这条检查在 dev 上**不会**触发 —— 那正是它必须写在**入库前**
    而不是靠数据库报错的原因。
    """
    dim = declared_dim()
    if dim is not None and len(v) != dim:
        return (
            None,
            EMBED_FAILED,
            f"embedding 维度不符：模型返回 {len(v)} 维，列声明 VECTOR({dim})",
        )
    return v, source, ""


def strict_embed(text: str) -> tuple[list[float] | None, str, str]:
    """**不许静默降级**的向量化 —— 官方语料灌入专用。返回 `(向量, 来源, 原因)`。

    ## 为什么不能复用 `embed_one`

    `embed_one` 在 real 失败时 `except Exception: pass` 后退回 64 维伪向量 ——
    这对**用户上传**是对的（几篇讲义，绝不因供应商抖动而阻塞）。
    但同一行为对**官方语料灌入**是危险的，实测（2026-06-12）：

        embedding 供应商（百炼）账户欠费 → HTTP 400
        → embed_one 吞掉异常，返回 64 维哈希词袋
        → 118（乃至全部 516）片被标成 embed_status='ok'
        → 检索看起来"有向量"，实际与关键词无异，且**没有任何告警**

    唯一把它顶出来的是 PG 那句难懂的 `expected 1024 dimensions, not 64`
    —— 而**在 SQLite / dev 上它会静默通过**。这是"假装成功的失败"，比报错更危险。

    所以这里把两件事明确分开：
    - **刻意**用 fake 模式（`embedding_mode` 既非 `real` 也非 `local`，离线开发 / 测试）→ 来源 `fake`，
      调用方应落 `embed_status='fake'`，让它在统计里**看得见**；
    - 配的是 real / local 但**调用失败、权重缺失 / 维度不符** → 来源 `failed` 且向量为 `None`。
      宁可让这批切片没有向量（检索按既有三级降级到关键词，且明写着 failed），
      也不让"看起来成功了"的假向量进库。

    ## `local` 模式的"暂时没权重"也归到失败（2026-06-16）

    本地模型最容易出的不是"调用报错"，而是**权重还没下**。若把它当成"没配"而退回伪向量，
    结果会与"灌了真向量"在库里长得一模一样（全是 `ok`）—— 正是上面这个坑的翻版。
    两种处置的代价不对称：宁可显式 failed（重灌一次只是几分钟），也不留"看起来有向量"的假象。
    """
    mode = settings.embedding_mode

    if mode == "local":
        from .local_embed import OnnxEmbedder

        try:
            v = _local_embed(text)
        except Exception as e:  # noqa: BLE001 — 失败**必须被看见**，理由见 docstring
            return None, EMBED_FAILED, f"local 加载失败 {type(e).__name__}: {str(e)[:160]}"
        if not v:
            return None, EMBED_FAILED, f"local 模式未取到向量（{OnnxEmbedder().status()}）"
        return _with_dim_check(v, EMBED_LOCAL)

    real = (
        mode == "real"
        and bool(settings.embedding_api_base)
        and bool(settings.embedding_api_key)
    )
    if not real:
        return _fake_embed(text), EMBED_FAKE, ""

    try:
        v = _real_embed(text)
    except Exception as e:  # noqa: BLE001 — 失败**必须被看见**，理由见 docstring
        return None, EMBED_FAILED, f"{type(e).__name__}: {str(e)[:200]}"
    if not v:
        return None, EMBED_FAILED, "real 模式未返回向量"

    return _with_dim_check(v, EMBED_REAL)


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
    """**旧口径**：二元组命中数（每个命中词项一律 +1）。

    保留是为了做**消融对照**（`eval/retrieval_eval.py` 把新旧稀疏通道并排跑一张表）——
    没有旧口径的数字，「换成 BM25 有效」这句话就无从证明。
    生产检索请用 `BigramBM25`。
    """
    terms = _terms(query)
    if not terms:
        return 0
    c = content or ""
    return sum(1 for t in terms if t in c)


def _unique_terms(text: str) -> list[str]:
    """去重保序的查询词项。

    BM25 按**词项**累加，重复词项不应重复计分 —— 查询里出现两次同一个二元组
    是查询表达式的问题，不是文档更相关的证据。
    """
    ordered: dict[str, None] = {}
    for t in _terms(text):
        ordered.setdefault(t, None)
    return list(ordered)


def query_terms(text: str) -> list[str]:
    """对外暴露的查询词项（去重保序）。

    检索层要用**与 BM25 完全一致**的词项去构造候选 SQL（PG 路径的 LIKE 粗筛）——
    两处若各切各的，候选集与打分口径就会悄悄错位。
    """
    return _unique_terms(text)


#: BM25 自由参数（Lucene 默认量级）。
#: k1 控制 TF 饱和速度（命中 1 次与命中 10 次的边际差异），
#: b 控制长度归一的强度（文档越长，单次命中越"便宜"）。
_BM25_K1 = 1.5
_BM25_B = 0.75


@dataclass(frozen=True)
class BigramBM25:
    """二元组上的 BM25（TF 饱和 + IDF + 文档长度归一）——**仅供消融对照，不是生产口径**。

    ## 实测结论：在本项目语料上，它**比命中数口径差**，因此未采纳

    在 `eval/datasets/官方法条/`（6 部法 / 415 片 / 编号类查询）上测量：

    | 口径 | recall@1 | recall@3 | MRR |
    | --- | --- | --- | --- |
    | 命中数（**生产**） | **0.7259** | **0.9333** | **0.8302** |
    | BM25 b=0.75（默认） | 0.2593 | 0.5556 | 0.4476 |
    | BM25 b=0.00 | 0.3481 | 0.5704 | 0.5050 |
    | BM25 b=0.00 k1=0.5（最好档） | 0.3778 | 0.6222 | 0.5426 |

    参数扫遍仍差一倍以上；并且**去掉标注偏袒后依然成立** ——
    用不带主题词的纯编号查询（n=120）复测：命中数 recall@1=0.40 vs BM25 0.10~0.18。
    抽查询人工核对过两种排序（如「义务教育法第一条」两者都把目标排第 1），
    确认**不是实现 bug，是机制差异**。

    ## 为什么 IDF 在这里失效（这是本次真正学到的东西）

    IDF 的前提是「罕见 ⇒ 信息量大」。但本场景的可检索信号长这样：

    - 法名的 bigram（`育法` / `法第`）**在条文正文里根本不存在** —— 法名只在 `heading_path` 里，
      所以它们对正文检索毫无贡献（idf 高，却匹配不到任何片）；
    - 条号的 bigram（`第一` / `一条`）**每一部法都有**，df 并不低 —— 它无法区分"哪部法的第一条"；
    - 剩下的内容词，正是 IDF 会压掉的那批中等频率词。

    于是 IDF 把**唯一还能用的信号压掉**，只剩下条号噪声。命中数口径不做这个假设，
    它只是问"这片命中了几个查询词" —— 在同质小语料上反而更稳。
    语料换成"主题词真正无处不在、且存在长尾专名"的大库时，结论可能反转（见 ADR-0015 的反转条件）。

    ## 原始动机（保留）

    换上 BM25 的动机是：命中数对每个词项一律加 1，「的」「学生」这类高频字组与
    「第七条」这类罕见词**权重完全相同**。这个诊断本身没错，错在**选错了药**：
    问题不是"没有 IDF"，而是"条号 bigram 不具区分性、法名 bigram 不在正文里"。

    公式（Lucene 口径，IDF 用 `ln(1 + (N-df+0.5)/(df+0.5))` 保证非负）：

        score(D,Q) = Σ_t IDF(t) · tf(t,D)·(k1+1) / ( tf(t,D) + k1·(1−b + b·|D|/avgdl) )

    `|D|` 按**词项数**（二元组个数）计，与词频同量纲。
    """

    tf: tuple[dict[str, int], ...]
    doc_len: tuple[int, ...]
    df: dict[str, int]
    n_docs: int
    avgdl: float
    k1: float = _BM25_K1
    b: float = _BM25_B

    @classmethod
    def fit(
        cls, contents: Sequence[str], k1: float = _BM25_K1, b: float = _BM25_B
    ) -> "BigramBM25":
        """建索引。`k1` / `b` 可覆盖 —— 参数必须能被**实验**扫（见 ADR-0015 的参数扫描表）。"""
        tfs: list[dict[str, int]] = []
        df: dict[str, int] = {}
        for text in contents:
            counts: dict[str, int] = {}
            for t in _terms(text or ""):
                counts[t] = counts.get(t, 0) + 1
            tfs.append(counts)
            for t in counts:  # df 按**文档**计：一片只算一次，与片内出现几次无关
                df[t] = df.get(t, 0) + 1
        n = len(tfs)
        total = sum(sum(c.values()) for c in tfs)
        return cls(
            tf=tuple(tfs),
            doc_len=tuple(sum(c.values()) for c in tfs),
            df=df,
            n_docs=n,
            avgdl=(total / n) if n else 0.0,
            k1=k1,
            b=b,
        )

    def idf(self, term: str) -> float:
        """逆文档频率。语料里没有的词项返回 0（它对任何文档都不构成证据）。"""
        df = self.df.get(term, 0)
        if df <= 0:
            return 0.0
        return math.log(1.0 + (self.n_docs - df + 0.5) / (df + 0.5))

    def score(self, query: str) -> list[float]:
        """对全部文档打分（与 `fit` 的 contents 顺序一一对应）。"""
        out = [0.0] * self.n_docs
        terms = _unique_terms(query)
        if not terms or not self.n_docs:
            return out
        norm = self.avgdl or 1.0
        k1, b = self.k1, self.b
        for t in terms:
            idf = self.idf(t)
            if idf <= 0.0:
                continue
            for i, counts in enumerate(self.tf):
                f = counts.get(t, 0)
                if not f:
                    continue
                dl = self.doc_len[i] or 1
                denom = f + k1 * (1.0 - b + b * dl / norm)
                out[i] += idf * (f * (k1 + 1.0)) / denom
        return out


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
    fn = embed_fn or embed_query  # 查询侧：带指令前缀（文档侧仍是不带前缀的 embed_one）
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

    # 通道 2：关键词检索（命中数口径 —— 与 kb_retrieval 的生产口径保持一致）
    scored = [(keyword_score(query, c.get("content") or ""), c) for c in pool]
    scored = [p for p in scored if p[0] > 0]
    if scored:
        scored.sort(key=lambda x: -x[0])
        return [c for _, c in scored[:k]]

    # 通道 3：均匀采样兜底
    return uniform_sample(pool, k)
