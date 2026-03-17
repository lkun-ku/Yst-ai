"""S1：连胜与补签卡。

补签卡**只由学习行为发放**（连胜里程碑等），本模块不提供任何分享/邀请/广告入口（诱导分享红线）。
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import get_current_candidate
from ..models import Candidate, Streak, StreakMakeup
from ..schemas import MakeupIn, MakeupOut, StreakOut
from ..services import streak as svc

router = APIRouter(prefix="/api/streak", tags=["streak"])


def _to_state(s: Streak) -> dict:
    return {
        "current": s.current,
        "max": s.max,
        "last_date": s.last_date,
        "cards": s.cards,
        "total_days": s.total_days,
    }


def _write(s: Streak, state: dict) -> None:
    s.current = state["current"]
    s.max = state["max"]
    s.last_date = state["last_date"]
    s.cards = state["cards"]
    s.total_days = state["total_days"]


def get_or_create(db: Session, candidate_id: int) -> Streak:
    s = db.query(Streak).filter(Streak.candidate_id == candidate_id).first()
    if s is None:
        st = svc.empty_state()
        s = Streak(
            candidate_id=candidate_id,
            current=st["current"],
            max=st["max"],
            last_date=st["last_date"],
            cards=st["cards"],
            total_days=st["total_days"],
        )
        db.add(s)
        db.flush()
    return s


def advance_on_daily_complete(db: Session, candidate_id: int) -> Streak:
    """完成每日任务时推进连胜，并在达到里程碑时发放补签卡。

    失败不影响每日任务完成（调用方应捕获），连胜是锦上添花而非主流程。
    """
    s = get_or_create(db, candidate_id)
    state = _to_state(s)
    today = date.today().isoformat()

    before = state["current"]
    state = svc.advance(state, today)

    # 里程碑发卡：记录已发过的里程碑，避免重复发放
    granted = {
        m
        for m in svc.CARD_MILESTONES
        if db.query(StreakMakeup)
        .filter(
            StreakMakeup.candidate_id == candidate_id,
            StreakMakeup.missed_date == f"milestone-{m}",
        )
        .first()
    }
    n = svc.cards_for_milestone(state["current"], granted)
    if n > 0:
        state = svc.grant_cards(state, n)
        for m in svc.CARD_MILESTONES:
            if state["current"] >= m and m not in granted:
                db.add(
                    StreakMakeup(
                        candidate_id=candidate_id,
                        missed_date=f"milestone-{m}",
                    )
                )

    _write(s, state)
    return s


@router.get("", response_model=StreakOut)
def get_streak(
    c: Candidate = Depends(get_current_candidate),
    db: Session = Depends(get_db),
) -> StreakOut:
    s = get_or_create(db, c.id)
    state = _to_state(s)
    used = {
        m.missed_date
        for m in db.query(StreakMakeup).filter(StreakMakeup.candidate_id == c.id).all()
    }

    # 建议补签日期：last_date 的前一天（最近漏掉的那天），且未补过
    makeup_date = None
    if state["last_date"]:
        cand = svc.prev_day(state["last_date"])
        if cand not in used:
            makeup_date = cand
    can_makeup = bool(makeup_date) and state["cards"] > 0

    return StreakOut(
        current=state["current"],
        max=state["max"],
        last_date=state["last_date"],
        cards=state["cards"],
        total_days=state["total_days"],
        can_makeup=can_makeup,
        makeup_date=makeup_date,
    )


@router.post("/makeup", response_model=MakeupOut)
def do_makeup(
    body: MakeupIn,
    c: Candidate = Depends(get_current_candidate),
    db: Session = Depends(get_db),
) -> MakeupOut:
    """补签指定日期（同一天幂等，由唯一约束 + used 集合双重保证）。"""
    d = (body.date or "").strip()
    if not d:
        raise HTTPException(400, "date 不能为空")

    s = get_or_create(db, c.id)
    used_rows = db.query(StreakMakeup).filter(StreakMakeup.candidate_id == c.id).all()
    used = {m.missed_date for m in used_rows}

    ok, new_state, why = svc.makeup(_to_state(s), d, used)
    if not ok:
        raise HTTPException(400, why)

    _write(s, new_state)
    db.add(StreakMakeup(candidate_id=c.id, missed_date=d))
    db.commit()

    return MakeupOut(ok=True, current=new_state["current"], cards=new_state["cards"])
