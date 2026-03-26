"""跨模块共用的小工具（无业务依赖，避免 router 之间互相 import）。

背景（#26）：「按本地日统计」此前在 `documents.py` 与 `kb.py` 各写一份且行为不一致——
- `documents` 版：naive datetime 视为 UTC，再转本地时区取日期；
- `kb` 版：直接截 `isoformat()[:10]`，得到的是 **UTC 日期**。

而比较基准 `date.today()` 是**本地日期**。在 UTC+8 的跨日时段（本地 00:00~08:00 对应 UTC 前一日），
kb 版会把当日创建的任务算成「昨天」，导致每日出题配额统计偏少、限额形同失效。
统一收口于此，两处共用同一实现。
"""

from __future__ import annotations

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
