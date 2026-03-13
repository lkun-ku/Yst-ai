"""额度与 VIP 权限（票 12 / Implementation 31 / 用户故事 #39-#45）。

- 免费每日 20 题跨局累计（以「当日发起的闯关局题目数之和」计），按自然日重置。
- 用尽后返回 429 + 友好提示（不强制付费、不弹窗强推）。
- VIP 不限量；内测阶段无真实支付（个人主体限制），vip-activate 为状态占位。
- 只对题量限速，不对内容设限：所有模块/考点免费均可刷（差异化关键）。
"""

from datetime import date, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session as DBSession

from ..db import get_db
from ..deps import get_current_candidate
from ..models import Candidate, Session
from ..schemas import QuotaOut, VipActivateOut

router = APIRouter(prefix="/api/quota", tags=["quota"])

FREE_DAILY_LIMIT = 20
RESET_RULE = "每日 0 点按本地时区自然日重置"


def used_today(db: DBSession, candidate_id: int, day: date) -> int:
    """今日已消耗额度：当日创建的闯关局 question_count 之和（Python 侧聚合，规避 SQLite 时区比较问题）。"""
    rows = (
        db.query(Session.created_at, Session.question_count)
        .filter(Session.candidate_id == candidate_id)
        .all()
    )
    used = 0
    for created_at, n in rows:
        if created_at is None:
            continue
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        local_day = created_at.astimezone().date()  # 服务器本地时区自然日
        if local_day == day and n:
            used += n
    return used


def enforce_quota(db: DBSession, c: Candidate, requested: int) -> None:
    """发起闯关时校验免费额度；VIP 不限量。超限抛 429 友好提示。"""
    if c.is_vip:
        return
    used = used_today(db, c.id, date.today())
    if used + requested > FREE_DAILY_LIMIT:
        remaining = max(0, FREE_DAILY_LIMIT - used)
        raise HTTPException(
            status_code=429,
            detail=(
                f"今日免费额度已用完（{FREE_DAILY_LIMIT} 题，剩余 {remaining}）。"
                f"明天再来继续刷；VIP 可不限量刷题，详见权益说明。"
            ),
        )


@router.get("", response_model=QuotaOut)
def get_quota(
    c: Candidate = Depends(get_current_candidate),
    db: DBSession = Depends(get_db),
) -> QuotaOut:
    used = used_today(db, c.id, date.today())
    return QuotaOut(
        is_vip=c.is_vip,
        free_daily_limit=FREE_DAILY_LIMIT,
        used_today=used,
        remaining=max(0, FREE_DAILY_LIMIT - used),
        reset_rule=RESET_RULE,
    )


@router.post("/vip-activate", response_model=VipActivateOut)
def vip_activate(
    c: Candidate = Depends(get_current_candidate),
    db: DBSession = Depends(get_db),
) -> VipActivateOut:
    """开通 VIP（内测占位：无真实支付链路，ADR-0001；正式版接个体工商户支付）。"""
    c.is_vip = True
    db.commit()
    return VipActivateOut(is_vip=True)
