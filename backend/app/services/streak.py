"""S1：连胜与补签卡的**纯逻辑**（不碰 DB，便于回归测试）。

设计要点：
- 连胜 = 连续完成每日任务的自然日数；同一天重复完成**幂等**（不重复 +1）。
- 补签卡上限 3 张，只能补**已过去且未计入连胜**的日期；同一天不可重复补（幂等）。
- **严禁分享/邀请/看广告发卡**：`grant_cards` 只由学习行为调用（连胜达标、正确率达标等）。
"""

from __future__ import annotations

from datetime import date, timedelta

MAX_MAKEUP_CARDS = 3

#: 连胜达标发放补签卡的里程碑
CARD_MILESTONES = (3, 7, 14)


def _parse(d: str) -> date:
    return date.fromisoformat(d)


def next_day(d: str) -> str:
    """返回 d 的后一天（YYYY-MM-DD）。"""
    return (_parse(d) + timedelta(days=1)).isoformat()


def prev_day(d: str) -> str:
    """返回 d 的前一天（YYYY-MM-DD）。"""
    return (_parse(d) - timedelta(days=1)).isoformat()


def empty_state() -> dict:
    return {"current": 0, "max": 0, "last_date": None, "cards": 0, "total_days": 0}


def advance(state: dict, today: str) -> dict:
    """完成一次每日任务后推进连胜。同一天重复调用幂等。"""
    s = dict(state or empty_state())
    last = s.get("last_date")

    if last == today:
        return s  # 幂等：今天已完成过
    if last and next_day(last) == today:
        s["current"] = int(s.get("current", 0)) + 1  # 连续
    else:
        s["current"] = 1  # 断连，重新计数

    s["max"] = max(int(s.get("max", 0)), s["current"])
    s["last_date"] = today
    s["total_days"] = int(s.get("total_days", 0)) + 1
    return s


def makeup(state: dict, missed_date: str, used: set[str] | list[str]) -> tuple[bool, dict, str]:
    """补签某一天。返回 `(ok, new_state, reason)`。

    约束：有余卡 → 该日期未补过 → 日期必须早于连胜最后一天（已过去且未计入）。
    """
    s = dict(state or empty_state())
    used_set = set(used or ())

    # 校验顺序刻意如此：先给"最根本"的原因（没有连胜 → 补签无从谈起），再谈资源与重复
    last = s.get("last_date")
    if not last:
        return False, s, "还没有连胜记录"
    if missed_date >= last:
        return False, s, "只能补已过去且未计入连胜的日期"
    if int(s.get("cards", 0)) <= 0:
        return False, s, "没有补签卡可用"
    if missed_date in used_set:
        return False, s, "该日期已补签过"

    s["current"] = int(s.get("current", 0)) + 1
    s["max"] = max(int(s.get("max", 0)), s["current"])
    s["cards"] = int(s.get("cards", 0)) - 1
    return True, s, ""


def grant_cards(state: dict, n: int = 1) -> dict:
    """发放补签卡（**仅由学习行为调用**），上限 MAX_MAKEUP_CARDS。"""
    s = dict(state or empty_state())
    s["cards"] = min(MAX_MAKEUP_CARDS, int(s.get("cards", 0)) + int(n))
    return s


def cards_for_milestone(current: int, already_granted: set[int] | list[int]) -> int:
    """连胜达到里程碑应发几张卡（每个里程碑只发一次）。"""
    granted = set(already_granted or ())
    return sum(1 for m in CARD_MILESTONES if current >= m and m not in granted)
