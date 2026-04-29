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


@dataclass(frozen=True)
class CitationGateMetrics:
    """引用闸门的**成对**度量（改造计划 §3 第 4 项）。

    **为什么必须成对看**：只看「流出编造率 = 0」是自欺 —— 若闸门根本没触发
    （开关关了、引用字段没人写、候选里压根没有引用），这个 0 与"闸门有效"
    在数字上**长得一模一样**。必须同时看「拦截率 > 0」：
    前者证明它拦得住，后者证明它确实在拦。

    两个口径的分母刻意不同：
    - 拦截率 = 拦下的已知编造 / **已知编造总数**（真值）→ 回答"该拦的拦住了吗"
    - 流出编造率 = 流出且真值为编造 / **通过总数**（实际输出）→ 回答"出去的东西干净吗"
    """

    n_total: int
    n_known_fabricated: int
    n_intercepted: int
    n_passed: int
    n_leaked_fabricated: int

    @property
    def interception_rate(self) -> float:
        if not self.n_known_fabricated:
            return 0.0
        return round(self.n_intercepted / self.n_known_fabricated, 4)

    @property
    def leaked_fabrication_rate(self) -> float:
        if not self.n_passed:
            return 0.0
        return round(self.n_leaked_fabricated / self.n_passed, 4)

    @property
    def false_negative_rate(self) -> float:
        """误杀率 = 真实引用被拦下的比例 —— 子串匹配的**已知代价**，必须一并上报。"""
        n_real = self.n_total - self.n_known_fabricated
        if not n_real:
            return 0.0
        return round((n_real - (self.n_passed - self.n_leaked_fabricated)) / n_real, 4)

    @property
    def sound(self) -> bool:
        """闸门是否"在干活且干对了"：有已知编造、拦下了、且一条都没漏放。"""
        return (
            self.n_known_fabricated > 0
            and self.interception_rate > 0
            and self.leaked_fabrication_rate == 0
        )

    def as_row(self) -> dict:
        return {
            "n": self.n_total,
            "known_fabricated": self.n_known_fabricated,
            "intercepted": self.n_intercepted,
            "passed": self.n_passed,
            "leaked": self.n_leaked_fabricated,
            "interception_rate": self.interception_rate,
            "leaked_fabrication_rate": self.leaked_fabrication_rate,
            "false_negative_rate": self.false_negative_rate,
            "sound": self.sound,
        }


def evaluate_citation_gate(
    cases: Sequence[dict],
    verify_fn: Callable[[str, Sequence], bool],
) -> CitationGateMetrics:
    """跑一遍带真值的引用用例，产出成对指标。

    `cases`     : `[{"quote": str, "chunks": [...], "fabricated": bool}, ...]`
    `verify_fn` : `(quote, chunks) -> bool`（True = 可定位、放行）

    与 `evaluate_retrieval` 同一设计：**指标不 import 校验器**，校验实现由调用方注入，
    这样换判定算法（子串 → 别的机制）时指标代码不动、历史数字仍可比。
    """
    n_fab = n_int = n_pass = n_leak = 0
    for c in cases:
        fab = bool(c.get("fabricated"))
        ok = bool(verify_fn(c.get("quote") or "", c.get("chunks") or []))
        if fab:
            n_fab += 1
            if not ok:
                n_int += 1
        if ok:
            n_pass += 1
        if fab and ok:
            n_leak += 1
    return CitationGateMetrics(
        n_total=len(cases),
        n_known_fabricated=n_fab,
        n_intercepted=n_int,
        n_passed=n_pass,
        n_leaked_fabricated=n_leak,
    )
