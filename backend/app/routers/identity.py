import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import get_current_candidate
from ..models import (
    Candidate,
    DailyTask,
    Mastery,
    MistakeBook,
    Session,
)

router = APIRouter(prefix="/api/identity", tags=["identity"])


class IdentityOut(BaseModel):
    candidate_id: int
    unionid: str
    is_guest: bool
    aigc_notice_acked: bool


class WechatLoginIn(BaseModel):
    unionid: str


def _to_out(c: Candidate) -> IdentityOut:
    return IdentityOut(
        candidate_id=c.id,
        unionid=c.unionid,
        is_guest=c.is_guest,
        aigc_notice_acked=c.aigc_notice_acked,
    )


@router.post("/guest", response_model=IdentityOut)
def guest_login(db: Session = Depends(get_db)) -> IdentityOut:
    """游客先玩：生成游客身份（本地保存，不强制登录）。"""
    unionid = f"guest_{uuid.uuid4().hex}"
    cand = Candidate(unionid=unionid, is_guest=True)
    db.add(cand)
    db.commit()
    db.refresh(cand)
    return _to_out(cand)


@router.post("/wechat-login", response_model=IdentityOut)
def wechat_login(body: WechatLoginIn, db: Session = Depends(get_db)) -> IdentityOut:
    """微信静默登录：以 unionid 为主键 upsert（ADR-0001 / Implementation 25）。

    真实环境由 code 换取 unionid；MVP 占位直接接收 unionid。
    """
    if not body.unionid:
        raise HTTPException(status_code=400, detail="unionid required")
    cand = db.query(Candidate).filter(Candidate.unionid == body.unionid).first()
    if cand is None:
        cand = Candidate(unionid=body.unionid, is_guest=False)
        db.add(cand)
    else:
        cand.is_guest = False
    db.commit()
    db.refresh(cand)
    return _to_out(cand)


@router.get("/me", response_model=IdentityOut)
def me(c: Candidate = Depends(get_current_candidate)) -> IdentityOut:
    return _to_out(c)


@router.post("/ack-aigc", response_model=IdentityOut)
def ack_aigc(
    c: Candidate = Depends(get_current_candidate), db: Session = Depends(get_db)
) -> IdentityOut:
    """首次说明：仅展示一次（Implementation 28 / 用户故事 #5）。"""
    c.aigc_notice_acked = True
    db.commit()
    db.refresh(c)
    return _to_out(c)


@router.delete("/data")
def delete_data(
    c: Candidate = Depends(get_current_candidate), db: Session = Depends(get_db)
) -> dict:
    """查看/删除学习数据（用户故事 #6）。删除该考生全部学习数据（D1：换 AppID 重置可彻底清除）。"""
    db.query(MistakeBook).filter(MistakeBook.candidate_id == c.id).delete()
    db.query(DailyTask).filter(DailyTask.candidate_id == c.id).delete()
    db.query(Mastery).filter(Mastery.candidate_id == c.id).delete()
    db.query(Session).filter(Session.candidate_id == c.id).delete()
    db.delete(c)
    db.commit()
    return {"status": "deleted"}
