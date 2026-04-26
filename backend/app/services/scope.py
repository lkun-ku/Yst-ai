"""Scope：检索过滤条件的**唯一载体**。

**为什么要有这个类**：过滤维度（命名空间 / 科目 / 学段 / 考点 / 文档）原本散落在各调用点，
每加一个维度就要改所有签名；**漏加一个维度就可能导致官方语料泄漏给别人** ——
而这种泄漏不会报错，只会让检索结果"看起来正常"。

收敛成一个不可变对象后：**加维度只改一处，越权风险也只在一处审计。**

**三条不变量（构造即校验，不做「尽量修正」）**：

| 不变量 | 为什么 |
| --- | --- |
| `namespace` 只能是三者之一 | 拼错字符串会静默退化成"不按命名空间过滤" |
| `official` 时 `candidate_id` 必须为 None | 官方语料**不属于任何考生**；带上 id 会暗示"某人拥有它" |
| `personal` / `both` 时 `candidate_id` 必须非空 | 为空等于**全库可见**，这是本项目最不能犯的错 |

**为什么在检索阶段就过滤，而不是先检索后过滤**：先检索后过滤会让无关（无权）文档
先进入候选集 —— 它们会挤占 top-k、污染 RRF 排名，并且**内容已经进了上下文**。
权限过滤必须是召回阶段的一部分，不是召回之后的一道筛子。
"""

from __future__ import annotations

from dataclasses import dataclass

#: 只查官方语料（考纲 / 法条 / rubric）
NAMESPACE_OFFICIAL = "official"
#: 只查某考生的个人资料
NAMESPACE_PERSONAL = "personal"
#: 本人资料 + 官方语料（最常见的问答场景）
NAMESPACE_BOTH = "both"

_ALL_NAMESPACES = (NAMESPACE_OFFICIAL, NAMESPACE_PERSONAL, NAMESPACE_BOTH)


@dataclass(frozen=True)
class Scope:
    """一次检索的完整过滤条件。所有字段均为可选，由不变量保证组合合法。"""

    namespace: str = NAMESPACE_PERSONAL
    candidate_id: int | None = None
    subject: str | None = None  # 领域包名（models.Subject 的枚举值）
    stage: str | None = None
    kp_ids: list[int] | None = None
    doc_ids: list[int] | None = None

    def __post_init__(self) -> None:
        if self.namespace not in _ALL_NAMESPACES:
            raise ValueError(
                f"namespace 必须是 {_ALL_NAMESPACES} 之一，收到 {self.namespace!r}"
            )
        if self.namespace == NAMESPACE_OFFICIAL and self.candidate_id is not None:
            raise ValueError(
                "官方语料不属于任何考生：namespace='official' 时 candidate_id 必须为 None"
            )
        if self.namespace in (NAMESPACE_PERSONAL, NAMESPACE_BOTH) and self.candidate_id is None:
            raise ValueError(
                f"namespace={self.namespace!r} 必须指定 candidate_id —— "
                "留空等于个人资料对全库可见"
            )

    def includes_official(self) -> bool:
        """本次检索是否包含官方语料。"""
        return self.namespace in (NAMESPACE_OFFICIAL, NAMESPACE_BOTH)

    def includes_personal(self) -> bool:
        """本次检索是否包含个人资料。"""
        return self.namespace in (NAMESPACE_PERSONAL, NAMESPACE_BOTH)

    def describe(self) -> str:
        """简短自述，用于日志与报错 —— 让"这次到底按什么范围查的"一眼可见。"""
        parts = [f"ns={self.namespace}"]
        if self.candidate_id is not None:
            parts.append(f"cand={self.candidate_id}")
        if self.subject:
            parts.append(f"subject={self.subject}")
        if self.stage:
            parts.append(f"stage={self.stage}")
        if self.kp_ids:
            parts.append(f"kp={len(self.kp_ids)}")
        if self.doc_ids:
            parts.append(f"doc={len(self.doc_ids)}")
        return " ".join(parts)
