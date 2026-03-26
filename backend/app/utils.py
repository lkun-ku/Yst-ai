"""跨模块共用的小工具（无业务依赖，避免 router 之间互相 import）。

背景（#26）：「按本地日统计」此前在 `documents.py` 与 `kb.py` 各写一份且行为不一致——
- `documents` 版：naive datetime 视为 UTC，再转本地时区取日期；
- `kb` 版：直接截 `isoformat()[:10]`，得到的是 **UTC 日期**。

而比较基准 `date.today()` 是**本地日期**。在 UTC+8 的跨日时段（本地 00:00~08:00 对应 UTC 前一日），
kb 版会把当日创建的任务算成「昨天」，导致每日出题配额统计偏少、限额形同失效。
统一收口于此，两处共用同一实现。
"""

from __future__ import annotations

import re
from datetime import datetime, timezone


def local_day(dt: datetime | None) -> str:
    """按**本地时区**取日期字符串（YYYY-MM-DD）。

    - dt 为 None → 空串
    - naive（无 tzinfo）→ 按 UTC 解释后再转本地
    - aware → 直接转本地时区
    """
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone().date().isoformat()


def parse_bloom_levels(value: str | None) -> list[str]:
    """解析逗号分隔的认知层级字符串（如 "understand,apply"）为空列表时返回 []。

    此前该解析在 kb.py / kb_generate.py / kb_graph.py 各写一份（#26 遗留 4）。
    """
    return [s.strip() for s in (value or "").split(",") if s.strip()]


def _shingles(text: str, n: int = 3) -> set[str]:
    """字符 n-gram 集合（去空白后），用于近似文本比较。"""
    s = re.sub(r"\s+", "", text or "")
    if not s:
        return set()
    return {s[i : i + n] for i in range(max(0, len(s) - n + 1))}


def near_duplicate(a: str, b: str, threshold: float = 0.9) -> bool:
    """判断两段文本是否近似重复（字符 3-gram 的 Jaccard 相似度 ≥ 阈值）。

    用途（#26 遗留 3）：拦住「字面微差」的重复题——精确去重（normalize_stem 后比集合）
    对「仅差一个空格/标点」这类无能为力。

    **为什么默认阈值高达 0.9**：实测两类样本的相似度分布是
    - 同模板生成的伪题（仅编号不同）：约 0.8
    - 语义相同但措辞不同的真重复：约 0.5~0.6

    二者区间相邻，0.75 会两头不讨好——既误杀伪题（12 题只剩 3 题），又漏掉真重复。
    因此默认保守（0.9）只拦几乎字面一致的重复，**宁可漏、不可误杀**（误杀会直接导致欠产）。
    阈值可由 DOC_STEM_DUP_THRESHOLD 调整。
    """
    sa, sb = _shingles(a), _shingles(b)
    if not sa or not sb:
        return False
    union = len(sa | sb)
    return union > 0 and (len(sa & sb) / union) >= threshold
