"""检索评测指标（纯函数，不读库、不联网、不依赖 LLM）。

**为什么单列一个模块**：指标本身必须可单测。若指标写错，后面所有「优化有效」的结论都不可信 ——
所以这里只做「排名列表 + 黄金答案」→ 数字的变换，IO 与路由全部留在调用方。

**黄金判定用子串，不用切片 id**：本仓的 eval 库每次运行都重建（见 `run_eval.py` 的说明），
切片 id 会变，而「内容里有没有这句话」稳定不变。这与 `source_quote` 校验同源 ——
零误判的布尔判定，且不依赖任何模型。

**recall 口径：前 k 个里"至少命中一个"黄金片段**，而不是"全部黄金片段都被召回"。
一个查询常对应多条相关切片，要求全覆盖会把 recall 压得极低、失去区分度；
「至少一个」回答的才是产品真正关心的问题：**答案有没有被捞进来**。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Iterable, Sequence

#: 默认考察的 k。语料是**小规模**的（评测数据集每域 6 片，见 datasets/），
#: 因此不取 k=10 —— 在 6 片语料上 recall@10 恒为 1.0，没有任何信息量。
DEFAULT_KS: tuple[int, ...] = (1, 2, 3)


def hit_of(chunk_content: str, gold_quotes: Sequence[str]) -> bool:
    """一个切片是否命中黄金内容：**任一**黄金片段是它的子串即算命中。"""
    content = chunk_content or ""
    return any(q and q in content for q in gold_quotes)


def hits_of(ranked: Sequence[str], gold_quotes: Sequence[str]) -> list[bool]:
    """把一条查询的有序结果转成命中位序列（供各指标复用）。"""
    return [hit_of(c, gold_quotes) for c in ranked]


def recall_at_k(hits_per_query: Sequence[Sequence[bool]], ks: Iterable[int] = DEFAULT_KS) -> dict[int, float]:
    """recall@k = 前 k 个结果里至少命中一个黄金片段的查询占比。"""
    ks = tuple(ks)
    if not hits_per_query:
        return {k: 0.0 for k in ks}
    return {
        k: round(sum(1 for h in hits_per_query if any(h[:k])) / len(hits_per_query), 4)
        for k in ks
    }


def mrr(hits_per_query: Sequence[Sequence[bool]]) -> float:
    """MRR = 首个命中位置倒数的均值（未命中记 0）——回答「正确答案排得靠前吗」。"""
    if not hits_per_query:
        return 0.0
    total = 0.0
    for h in hits_per_query:
        rank = next((i + 1 for i, ok in enumerate(h) if ok), 0)
        if rank:
            total += 1.0 / rank
    return round(total / len(hits_per_query), 4)


def ndcg_at_k(hits_per_query: Sequence[Sequence[bool]], k: int) -> float:
    """nDCG@k（二值相关性）：越靠前命中得分越高，用来区分「都召回了但顺序不同」。

    本仓的相关性只有两档（黄金片段 / 其它），所以 IDCG 取「理想排序」下的 DCG。
    """
    if not hits_per_query or k <= 0:
        return 0.0
    total = 0.0
    for h in hits_per_query:
        dcg = sum(1.0 / math.log2(i + 2) for i, ok in enumerate(h[:k]) if ok)
        ideal = min(sum(1 for ok in h if ok), k)
        idcg = sum(1.0 / math.log2(i + 2) for i in range(ideal))
        total += (dcg / idcg) if idcg else 0.0
    return round(total / len(hits_per_query), 4)


def citation_hit_rate(quotes: Sequence[str], chunks: Sequence[str]) -> float:
    """引用命中率：被引用的片段中，有多少真的出现在给定切片里。

    这是「零编造」的度量基础 —— 与 `quote in chunk.content` 同一判定，不做语义近似。
    注意它衡量的是**首次通过率**；若校验失败会触发重生成，最终输出恒为 1.0、失去信息量。
    """
    quotes = [q for q in quotes if q]
    if not quotes:
        return 0.0
    ok = sum(1 for q in quotes if any(q in (c or "") for c in chunks))
    return round(ok / len(quotes), 4)


@dataclass(frozen=True)
class RetrievalMetrics:
    """一次检索配置在标注集上的结果。"""

    recall_at_k: dict[int, float]
    mrr: float
    ndcg: float
    n_queries: int
    ks: tuple[int, ...]

    def as_row(self) -> dict:
        """拍平成一行，便于写表格（列顺序稳定，便于人工比对两次运行）。"""
        row: dict = {"n": self.n_queries}
        row.update({f"recall@{k}": v for k, v in self.recall_at_k.items()})
        row["mrr"] = self.mrr
        row["ndcg"] = self.ndcg
        return row


def evaluate_retrieval(
    labeled: Sequence[dict],
    retrieve_fn: Callable[[str], Sequence[str]],
    ks: Sequence[int] = DEFAULT_KS,
    ndcg_k: int | None = None,
) -> RetrievalMetrics:
    """跑一遍带标注的查询，产出检索指标。

    `labeled`   : `[{"query": str, "gold": [黄金片段子串, ...]}, ...]`
    `retrieve_fn`: `query -> 有序切片正文列表`

    **`retrieve_fn` 就是消融实验的接口** —— 换一个函数即换一档检索配置，
    指标代码完全不用改。这也是本模块不 import `kb_retrieval` 的原因：避免把
    「指标」与「某一条检索实现」绑死。
    """
    ks = tuple(ks)
    per_query = [
        hits_of(list(retrieve_fn(item["query"])), item.get("gold") or []) for item in labeled
    ]
    return RetrievalMetrics(
        recall_at_k=recall_at_k(per_query, ks),
        mrr=mrr(per_query),
        ndcg=ndcg_at_k(per_query, ndcg_k or max(ks, default=1)),
        n_queries=len(labeled),
        ks=ks,
    )
