"""实时生成用量与池化率监控（票 14 / Implementation 14、17）。

- 每人每日实时生成上限 3 次（变式 + 复盘段落共用额度），超限自动降级纯池化，考生无感。
- 池化率 = 1 - 实时生成占比；实时占比 > 5% 触发告警（池化率 < 95%）。
"""

from datetime import date, datetime, timezone

from sqlalchemy.orm import Session as DBSession

from ..models import RealtimeUsage, Session

REALTIME_DAILY_LIMIT = 3
POOL_RATIO_THRESHOLD = 0.95  # 池化率 ≥95% 硬约束


def get_today_usage(db: DBSession, candidate_id: int, day: date) -> int:
    row = (
        db.query(RealtimeUsage)
        .filter(RealtimeUsage.candidate_id == candidate_id, RealtimeUsage.day == day.isoformat())
        .first()
    )
    return row.count if row else 0


def increment_usage(db: DBSession, candidate_id: int, day: date) -> None:
    row = (
        db.query(RealtimeUsage)
        .filter(RealtimeUsage.candidate_id == candidate_id, RealtimeUsage.day == day.isoformat())
        .first()
    )
    if row is None:
        row = RealtimeUsage(candidate_id=candidate_id, day=day.isoformat(), count=0)
        db.add(row)
    row.count += 1
    db.commit()


def realtime_share(db: DBSession, day: date) -> tuple[float, bool, int]:
    """返回 (实时生成占比, 是否告警, 实时次数)。占比 = 实时次数 / (实时次数 + 当日池化下发题量)。"""
    realtime_today = (
        db.query(RealtimeUsage)
        .filter(RealtimeUsage.day == day.isoformat())
        .all()
    )
    realtime_count = sum(r.count for r in realtime_today)

    served = 0
    for created_at, n in db.query(Session.created_at, Session.question_count).all():
        if created_at is None or not n:
            continue
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        if created_at.astimezone().date() == day:
            served += n

    share = realtime_count / max(1, realtime_count + served)
    return share, (1 - share) < POOL_RATIO_THRESHOLD, realtime_count
