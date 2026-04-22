"""题库去重（P1）：三层拦截 —— 精确 / 近似 / 语义。

## 为什么必须是三层

改造前，官方池只有一层判重：`(module, knowledge_point, stem)` **元组全等**。
而文档出题链路早就有两层（精确 + 字符 3-gram Jaccard）。这个落差带来两类漏网：

| 层 | 手段 | 拦得住 | 拦不住 |
| --- | --- | --- | --- |
| L1 精确 | `normalize_stem` 后全等 | 一字不差（含标点/空白微差） | 换个说法 |
| L2 近似 | 字符 3-gram Jaccard ≥ 阈值 | 只改标点、换个措辞 | 完全改写 |
| L3 语义 | 题干向量余弦 ≥ 阈值 | **考点 + 干扰项同构、字面完全不同** | —— |

L3 才是关键：AI 出题最典型的灌水不是「复制粘贴」，而是**同一个考点换个背景故事又出一遍**——
题干长得完全不一样，L1/L2 都看不见。

## 阈值的两个坑

1. **官方池不能用文档侧的 0.6**。官方变式题是「同考点出多道」，模型天然倾向同一模板，
   题干高度相似是**正常现象**；0.6 会把同考点的正常变式成批误杀。
   故官方池用 `official_stem_dup_threshold`（默认 0.85），只拦几乎字面一致。
2. **同模板伪题必须整体跳过快速判重**。fake 实现产出的占位题彼此相似度约 0.8，
   任何近似阈值都会把它们成批误杀（实测 12 题只剩 3 题）。
   这个属性属于**生成器**而不是全局配置，故判据挂在 `client.produces_varied_stems` 上——
   测试会在 real 模式下注入 FakeLLMClient，挂在全局开关上会漏判。

## L3 的作用域（本阶段的边界）

L3 只能比对**内存中拿得到向量的题**。当前仅用于**同一轮生成之内**的互查
（这一轮新出的题彼此之间）。跨库的语义判重需要把题干向量持久化
（`questions` 上再加一列），留待官方知识库落地时一并做——那时 embedding 基建已经就绪。
"""

from __future__ import annotations

from ..config import settings
from ..models import Question, QuestionSource, ProofreadStatus
from .doc_parser import normalize_stem
from ..utils import near_duplicate

#: 命中层级。返回值带上层级是为了能分辨「拦了多少精确重复」与「拦了多少语义灌水」——
#: 前者说明生成管线在重复劳动，后者说明模型在同一个考点上打转，是两种不同的问题。
LEVEL_EXACT = "exact"
LEVEL_NEAR = "near"
LEVEL_SEMANTIC = "semantic"

#: 同模板伪题标志（`LLMClient.produces_varied_stems=False`）下判重整体退化为精确层；
#: 语义层同理（伪题向量天然高度相似）。
_PLACEHOLDER_NOTE = "生成器产出同模板伪题，仅做精确判重"


def find_duplicate(
    stem: str,
    *,
    existing: list[tuple[int | None, str]] | None = None,
    enable_near: bool = True,
    threshold: float | None = None,
    vec: list[float] | None = None,
    existing_vecs: list[tuple[int | None, list[float]]] | None = None,
) -> tuple[str | None, int | None]:
    """在既有题目集合里查找与 `stem` 重复的题。

    返回 `(命中层级, 既有题 id)`；未命中返回 `(None, None)`。

    - `existing`：`[(题目 id, 题干)]`，**必须限定在同一考点内**（见 `load_official_index`）。
    - `enable_near=False`：只做精确判重（同模板伪题的场景）。
    - `vec` / `existing_vecs`：可选。两者都给且非空时才启用 L3 语义判重。
      既有题 id 允许为 None（同轮内刚 add、尚未 flush 的题）。
    """
    key = normalize_stem(stem)
    if not key:
        return (None, None)

    candidates = existing or []
    for qid, raw in candidates:
        if normalize_stem(raw) == key:
            return (LEVEL_EXACT, qid)

    if enable_near:
        limit = threshold if threshold is not None else settings.official_stem_dup_threshold
        for qid, raw in candidates:
            if near_duplicate(key, normalize_stem(raw), limit):
                return (LEVEL_NEAR, qid)

    if vec and existing_vecs:
        from .embedding import cosine_similarity  # 局部导入：仅语义层需要 numpy

        limit = settings.official_stem_semantic_threshold
        for qid, other in existing_vecs:
            if cosine_similarity(vec, other) >= limit:
                return (LEVEL_SEMANTIC, qid)

    return (None, None)


def load_official_index(db) -> dict[tuple[str, str], list[tuple[int, str]]]:
    """读回官方池题干，按 `(模块 value, 考点名)` 分桶，供逐考点判重使用。

    **为什么分桶，而不是像改造前那样拉一个全局集合：**

    - 精确层跨考点比对收益为零（不同考点本就该有不同的题）；
    - 近似层跨考点比对会**系统性误杀**——「受教育权」与「受教育权保护」这类相邻考点名
      会让题干天然相似度很高，全局比对会把它们判成重复题。

    只取 `未驳回` 的题：判重的语义是「这道题库里**已经有能用的**了」，
    被人工驳回的题不该长期阻塞同题重出。
    """
    rows = (
        db.query(Question.id, Question.module, Question.knowledge_point, Question.stem)
        .filter(
            Question.source == QuestionSource.POOL,
            Question.proofread_status != ProofreadStatus.REJECTED,
        )
        .all()
    )
    index: dict[tuple[str, str], list[tuple[int, str]]] = {}
    for qid, module, knowledge_point, stem in rows:
        # module 为枚举成员（SAEnum 读回即枚举）；.value 即中文模块名，
        # 与 KNOWLEDGE_POINTS 的键一致，调用方可直接用 module.value 取桶。
        module_value = getattr(module, "value", module)
        index.setdefault((module_value, knowledge_point), []).append((qid, stem or ""))
    return index


def bucket_of(
    index: dict[tuple[str, str], list[tuple[int, str]]], module_value: str, knowledge_point: str
) -> list[tuple[int, str]]:
    """取某考点的题干桶（不存在时创建并挂回 index，保证后续新增题也进桶）。"""
    return index.setdefault((module_value, knowledge_point), [])
